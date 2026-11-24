"""Execute a plan safely: write-ahead journal, paired .meta moves, real rollback.

Three rules this module exists to enforce:

1. **Validate everything before touching anything.** A failure in the middle of
   a 200-file rename is exactly the situation no user can recover from by hand.
2. **Never move a file without its `.meta`.** The `.meta` carries the GUID, and
   the GUID is what every scene/prefab points at. Moving the asset alone and
   letting Unity rebuild the meta is how references die.
3. **Journal before acting, not after.** The rollback file is updated *before*
   each filesystem operation, so a crash (or Ctrl+C) still leaves a recoverable
   record.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .metaedit import Edit, edit_meta_text, read_meta_text
from .metainfo import parse_meta, parse_meta_text
from .plan import Operation, Plan, tree_digest, verify_sources
from .project import ProjectInfo

JOURNAL_SCHEMA = "unityguard.journal/1"
JOURNAL_DIR = ".unityguard"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _sha256_str(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".ugtmp")
    temp.write_text(text, encoding="utf-8", newline="")
    os.replace(temp, path)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".ugtmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def _write_backup(journal: "Journal", project: ProjectInfo, rel: str, text: str) -> str:
    """Keep the original bytes so rollback is exact, even for formatting."""
    backup = journal.path.parent.parent / "backups" / journal.path.stem / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.joinpath(backup.name).write_text(text, encoding="utf-8", newline="")
    try:
        return project.rel(backup)
    except ValueError:
        return str(backup)


@dataclass
class Journal:
    """Rollback record. Written to `.unityguard/journals/` inside the project
    (a dot-folder, which Unity ignores)."""

    path: Path
    payload: dict
    entries: list[dict] = field(default_factory=list)

    @classmethod
    def create(cls, project: ProjectInfo, plan: Plan, tier: str) -> "Journal":
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        directory = project.root / JOURNAL_DIR / "journals"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"rollback_{stamp}.json"
        payload = {
            "schema": JOURNAL_SCHEMA,
            "project_root": str(project.root),
            "plan_hash": plan.digest(),
            "tier": tier,
            "started_at": _now(),
            "finished_at": None,
            "completed": False,
            "rolled_back_at": None,
            "entries": [],
        }
        journal = cls(path=path, payload=payload, entries=payload["entries"])
        journal.save()
        return journal

    def save(self) -> None:
        self.payload["entries"] = self.entries
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, op: Operation, state: str, created_dirs: list[str]) -> dict:
        entry = {
            "op_id": op.op_id,
            "kind": op.kind,
            "from": op.from_rel,
            "to": op.to_rel,
            "from_meta": op.from_meta_rel,
            "to_meta": op.to_meta_rel,
            "sha256": op.sha256,
            "meta_sha256": op.meta_sha256,
            "guid": op.guid,
            "is_dir": op.is_dir,
            "tree_hash": op.tree_hash,
            "file_count": op.file_count,
            "edits": [],
            "backup_rel": None,
            "old_sha256": None,
            "new_sha256": None,
            "created_dirs": created_dirs,
            "created_metas": [],
            "state": state,
            "applied_at": None,
        }
        self.entries.append(entry)
        self.save()
        return entry

    def mark(self, entry: dict, state: str) -> None:
        entry["state"] = state
        if state == "done":
            entry["applied_at"] = _now()
        self.save()

    def finish(self) -> None:
        self.payload["completed"] = True
        self.payload["finished_at"] = _now()
        self.save()


@dataclass
class ApplyResult:
    dry_run: bool
    applied: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    journal_path: str | None = None
    guid_checks: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def _move(src: Path, dst: Path) -> None:
    """Move a single file, refusing to overwrite, handling case-only renames."""
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src}")
    if dst.exists() and os.path.normcase(str(dst)) != os.path.normcase(str(src)):
        raise FileExistsError(f"目标已存在，拒绝覆盖：{dst}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    only_case_changed = (os.path.normcase(str(src)) == os.path.normcase(str(dst))
                         and str(src) != str(dst))
    if only_case_changed:
        # Windows keeps the old casing on a direct rename.
        temp = src.with_name(src.name + ".ugtmp")
        os.rename(src, temp)
        os.rename(temp, dst)
        return
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            shutil.move(str(src), str(dst))
        else:
            raise


def _rewrite_created_dirs(project: ProjectInfo, rel_path: str) -> list[str]:
    """Create parent folders, returning the ones that did not exist before."""
    target = (project.root / rel_path).parent
    missing: list[str] = []
    cursor = target
    while not cursor.exists() and cursor != project.root:
        missing.append(project.rel(cursor))
        cursor = cursor.parent
    target.mkdir(parents=True, exist_ok=True)
    return list(reversed(missing))


def _folder_meta_text() -> str:
    import uuid

    return (
        "fileFormatVersion: 2\n"
        f"guid: {uuid.uuid4().hex}\n"
        "folderAsset: yes\n"
        "DefaultImporter:\n"
        "  externalObjects: {}\n"
        "  userData: \n"
        "  assetBundleName: \n"
        "  assetBundleVariant: \n"
    )


def _ensure_parent_chain(project: ProjectInfo, rel_dir: str) -> tuple[list[str], list[str]]:
    """Create the missing part of `rel_dir`, giving every new folder a `.meta`.

    Unity wants a `.meta` for *every* folder. Creating a folder without one and
    letting the editor invent its own GUID is how you end up with a project
    where half the folder GUIDs are "whatever happened that day".
    """
    parts = rel_dir.split("/")
    created_dirs: list[str] = []
    created_metas: list[str] = []
    current = project.root / parts[0]
    for part in parts[1:]:
        nxt = current / part
        if not nxt.exists():
            nxt.mkdir()
            created_dirs.append(project.rel(nxt))
            meta = Path(str(nxt) + ".meta")
            if not meta.exists():
                meta.write_text(_folder_meta_text(), encoding="utf-8")
                created_metas.append(project.rel(meta))
        current = nxt
    return created_dirs, created_metas


def _dest_matches(project: ProjectInfo, op: Operation) -> bool:
    """Does the destination already hold exactly what this operation wants there?

    Checking the destination (not the source) is what makes swopped names and
    chains recognisable: after `a↔b`, both paths exist again, so "does the source
    still exist?" is the wrong question.
    """
    dst = project.root / op.to_rel
    if op.kind == "meta_edit":
        meta = project.root / (op.from_meta_rel or "")
        if not meta.is_file():
            return False
        return bool(op.new_meta_sha256) and _sha256(meta) == op.new_meta_sha256
    if not dst.exists():
        return False
    if op.is_dir:
        if not dst.is_dir():
            return False
        if op.tree_hash:
            current, _, _ = tree_digest(dst)
            return current == op.tree_hash
        return True
    if not dst.is_file():
        return False
    if op.sha256:
        return _sha256(dst) == op.sha256
    return dst.stat().st_size == op.size


def _plan_state(project: ProjectInfo, plan: Plan) -> str:
    """`done` / `partial` / `pending` for plans that use temporary swap hops."""
    temps = [op for op in plan.operations if op.is_temp]
    real = [op for op in plan.operations if not op.is_temp]
    # A file parked under a temporary name means the swap stopped halfway.
    if any((project.root / op.to_rel).exists() for op in temps):
        return "partial"
    matched = sum(1 for op in real if _dest_matches(project, op))
    if real and matched == len(real):
        return "done"
    if matched:
        return "partial"
    return "pending"


def apply_plan(project: ProjectInfo, plan: Plan, *, dry_run: bool = False,
               force: bool = False, tier: str = "free",
               fail_after: int | None = None) -> ApplyResult:
    """`fail_after` is a test hook that simulates a crash mid-run."""
    result = ApplyResult(dry_run=dry_run)

    if not plan.operations:
        result.failed.append("计划里没有可执行的操作")
        return result

    lock = project.root / "Temp" / "UnityLockfile"
    if lock.exists() and not force:
        result.failed.append(
            "Unity 编辑器正打开这个工程（Temp/UnityLockfile）。"
            "请先关闭 Unity 再执行：编辑器运行时改名，Unity 会把「旧文件消失 + 新文件出现」"
            "当成两个不同资源，GUID 引用会断。"
        )
        return result

    has_temps = any(op.is_temp for op in plan.operations)
    if has_temps:
        state = _plan_state(project, plan)
        if state == "done":
            result.messages.append("这个计划已经全部执行过了，没有需要再做的操作。")
            return result
        if state == "partial":
            result.failed.append(
                "检测到上一次互换没有做完（有文件停留在临时名 .ugswap-* 上）。"
                "请先用 rollback 撤回到执行前的状态，再重新执行这个计划。")
            return result
        pending = list(plan.operations)
    else:
        # Destination-based: a chain (b→c then a→b) can resume correctly, and
        # a finished plan is recognised even though the sources exist again.
        pending = [op for op in plan.operations if not _dest_matches(project, op)]
        result.skipped = len(plan.operations) - len(pending)
        if not pending:
            result.messages.append("这个计划已经全部执行过了，没有需要再做的操作。")
            return result
        if result.skipped:
            result.messages.append(f"{result.skipped} 个操作在此前已经完成，本次跳过。")

    run = Plan(project_root=plan.project_root, operations=pending, rule=plan.rule)
    problems = verify_sources(project, run, force=force)
    if problems:
        result.failed.extend(problems)
        result.messages.append("前置校验失败，未修改任何文件。")
        return result

    if dry_run:
        result.messages.append(f"dry-run：{len(pending)} 个操作待执行，未写盘。")
        return result

    journal = Journal.create(project, run, tier)
    result.journal_path = str(journal.path)

    for index, op in enumerate(pending, start=1):
        if fail_after is not None and index > fail_after:
            result.failed.append(f"（测试注入）在第 {index} 个操作前中止")
            break

        entry = journal.add(op, "pending", [])
        try:
            if op.kind == "meta_edit":
                meta_path = project.root / (op.from_meta_rel or "")
                original = read_meta_text(meta_path)
                edits = [Edit.from_dict(item) for item in op.edits]
                new_text, applied_edits, problems = edit_meta_text(original, edits)
                if problems:
                    raise RuntimeError("；".join(problems))
                guid_after = parse_meta_text(new_text, op.from_meta_rel or "").guid
                if op.guid and guid_after != op.guid:
                    raise RuntimeError(f"GUID 发生变化（{op.guid} → {guid_after}）")

                backup = _write_backup(journal, project, op.from_meta_rel or "", original)
                entry["backup_rel"] = backup
                entry["edits"] = [edit.to_dict() for edit in applied_edits]
                entry["old_sha256"] = _sha256_str(original)
                entry["new_sha256"] = _sha256_str(new_text)
                _write_text_atomic(meta_path, new_text)

                journal.mark(entry, "done")
                result.applied += 1
                result.guid_checks += 1
                continue

            src = project.root / op.from_rel
            dst = project.root / op.to_rel
            target_parent = str(Path(op.to_rel).parent.as_posix())
            created, created_metas = _ensure_parent_chain(project, target_parent)
            entry["created_dirs"] = created
            entry["created_metas"] = created_metas

            _move(src, dst)

            if op.from_meta_rel and op.to_meta_rel:
                meta_src = project.root / op.from_meta_rel
                meta_dst = project.root / op.to_meta_rel
                if meta_src.is_file():
                    _move(meta_src, meta_dst)
                    # The GUID is the whole point: it must survive the move.
                    guid_before = op.guid
                    guid_after = parse_meta(meta_dst, op.to_meta_rel).guid
                    if guid_before and guid_after != guid_before:
                        raise RuntimeError(
                            f"GUID 发生变化（{guid_before} → {guid_after}）：{op.to_meta_rel}")
                    result.guid_checks += 1

            journal.mark(entry, "done")
            result.applied += 1
        except Exception as exc:  # noqa: BLE001 - we report, never swallow
            journal.mark(entry, "failed")
            result.failed.append(f"{op.from_rel}: {exc}")
            break

    if result.failed:
        result.messages.append(
            f"已中止。已完成的 {result.applied} 个操作可以用回滚日志恢复：{journal.path}")
    else:
        journal.finish()
        result.messages.append(f"执行完成，回滚日志：{journal.path}")
    return result


def rollback_journal(journal_path: Path, *, dry_run: bool = False,
                     force: bool = False) -> ApplyResult:
    payload = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    if payload.get("schema") != JOURNAL_SCHEMA:
        raise ValueError(f"不是 UnityGuard 回滚日志：{payload.get('schema')!r}")

    root = Path(payload["project_root"])
    entries = [entry for entry in payload.get("entries", [])
               if entry.get("state") in ("done", "pending", "failed")]
    result = ApplyResult(dry_run=dry_run)

    if payload.get("rolled_back_at"):
        result.messages.append(f"这个日志已经在 {payload['rolled_back_at']} 回滚过。")

    for entry in reversed(entries):
        if entry.get("kind") == "meta_edit":
            meta_path = root / (entry.get("from_meta") or "")
            backup = root / entry["backup_rel"] if entry.get("backup_rel") else None
            if backup is None or not backup.is_file():
                result.failed.append(f"备份缺失，无法恢复：{entry.get('from_meta')}")
                continue
            if not meta_path.is_file():
                result.failed.append(f".meta 已不存在：{entry.get('from_meta')}")
                continue
            if not force and entry.get("new_sha256"):
                if _sha256(meta_path) != entry["new_sha256"]:
                    result.failed.append(
                        f"回滚冲突：{entry['from_meta']} 在修复之后又被改过，拒绝覆盖")
                    continue
            if dry_run:
                result.skipped += 1
                continue
            _write_bytes_atomic(meta_path, backup.read_bytes())
            result.applied += 1
            continue

        src = root / entry["from"]
        dst = root / entry["to"]
        meta_src = root / entry["from_meta"] if entry.get("from_meta") else None
        meta_dst = root / entry["to_meta"] if entry.get("to_meta") else None

        if not dst.exists():
            result.skipped += 1
            continue
        if src.exists():
            result.failed.append(f"回滚冲突：{entry['from']} 已经存在，跳过 {entry['to']}")
            continue

        if not force:
            if entry.get("is_dir") and entry.get("tree_hash"):
                current, _, _ = tree_digest(dst)
                if current != entry["tree_hash"]:
                    result.failed.append(
                        f"文件夹内容已变化，拒绝回滚：{entry['to']}"
                        "（移动之后里面被改过，回滚会覆盖新内容）")
                    continue
            elif entry.get("sha256"):
                current = _sha256(dst)
                if current and current != entry["sha256"]:
                    result.failed.append(
                        f"内容已变化，拒绝回滚：{entry['to']}"
                        "（目标文件在体检之后被改过，回滚会覆盖新内容）")
                    continue

        if dry_run:
            result.skipped += 1
            continue

        _move(dst, src)
        if meta_dst is not None and meta_dst.is_file() and meta_src is not None:
            if meta_src.exists():
                result.failed.append(f"回滚冲突：{entry['from_meta']} 已存在")
            else:
                _move(meta_dst, meta_src)
        result.applied += 1

    if not dry_run:
        # Folder `.meta` files this run invented must not survive the rollback.
        for entry in entries:
            for rel in entry.get("created_metas", []):
                meta_path = root / rel
                try:
                    if meta_path.is_file():
                        meta_path.unlink()
                except OSError:
                    pass

    # Remove folders this run created, deepest first, only if still empty.
    created: set[str] = set()
    for entry in entries:
        created.update(entry.get("created_dirs", []))
    for rel in sorted(created, key=len, reverse=True):
        directory = root / rel
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass

    if not dry_run:
        payload["rolled_back_at"] = _now()
        path = Path(journal_path)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result.messages.append(f"已回滚 {result.applied} 个操作")
    else:
        result.messages.append(f"dry-run：将回滚 {len(entries)} 个操作")
    return result
