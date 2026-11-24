"""Build and validate a change plan: the object you review *before* anything moves.

Design rule: the plan is the single source of truth. `apply` only executes a
plan file, never ad-hoc arguments, so what you reviewed is exactly what runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .metainfo import parse_meta
from .project import ProjectInfo, ignored_by_unity

PLAN_SCHEMA = "unityguard.plan/1"

_INVALID_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_HASH_LIMIT = 64 * 1024 * 1024


@dataclass
class Operation:
    op_id: int
    kind: str                     # "rename" | "move" | "move_dir"
    from_rel: str
    to_rel: str
    from_meta_rel: str | None = None
    to_meta_rel: str | None = None
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None
    meta_sha256: str | None = None
    guid: str | None = None
    reason: str = ""
    is_dir: bool = False
    tree_hash: str | None = None
    file_count: int = 0
    edits: list[dict] = field(default_factory=list)
    new_meta_sha256: str | None = None
    is_temp: bool = False

    def to_dict(self) -> dict:
        return {
            "op_id": self.op_id,
            "kind": self.kind,
            "from": self.from_rel,
            "to": self.to_rel,
            "from_meta": self.from_meta_rel,
            "to_meta": self.to_meta_rel,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "meta_sha256": self.meta_sha256,
            "new_meta_sha256": self.new_meta_sha256,
            "guid": self.guid,
            "reason": self.reason,
            "is_dir": self.is_dir,
            "tree_hash": self.tree_hash,
            "file_count": self.file_count,
            "edits": self.edits,
            "is_temp": self.is_temp,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Operation":
        return cls(
            op_id=payload["op_id"],
            kind=payload["kind"],
            from_rel=payload["from"],
            to_rel=payload["to"],
            from_meta_rel=payload.get("from_meta"),
            to_meta_rel=payload.get("to_meta"),
            size=payload.get("size", 0),
            mtime_ns=payload.get("mtime_ns", 0),
            sha256=payload.get("sha256"),
            meta_sha256=payload.get("meta_sha256"),
            guid=payload.get("guid"),
            reason=payload.get("reason", ""),
            is_dir=payload.get("is_dir", False),
            tree_hash=payload.get("tree_hash"),
            file_count=payload.get("file_count", 0),
            edits=list(payload.get("edits", [])),
            new_meta_sha256=payload.get("new_meta_sha256"),
            is_temp=payload.get("is_temp", False),
        )


@dataclass
class Plan:
    project_root: str
    operations: list[Operation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rule: str = "manual"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    @property
    def is_valid(self) -> bool:
        return not self.errors and bool(self.operations)

    def digest(self) -> str:
        canonical = json.dumps(
            [op.to_dict() for op in self.operations],
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def to_dict(self) -> dict:
        return {
            "schema": PLAN_SCHEMA,
            "created_at": self.created_at,
            "project_root": self.project_root,
            "rule": self.rule,
            "plan_hash": self.digest(),
            "summary": {
                "operations": len(self.operations),
                "renames": sum(1 for op in self.operations if op.kind == "rename"),
                "moves": sum(1 for op in self.operations if op.kind == "move"),
                "folder_moves": sum(1 for op in self.operations if op.kind == "move_dir"),
                "meta_edits": sum(1 for op in self.operations if op.kind == "meta_edit"),
                "affected_bytes": sum(op.size for op in self.operations),
            },
            "warnings": self.warnings,
            "errors": self.errors,
            "operations": [op.to_dict() for op in self.operations],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Plan":
        if payload.get("schema") != PLAN_SCHEMA:
            raise ValueError(f"不是 UnityGuard 计划文件：schema={payload.get('schema')!r}")
        return cls(
            project_root=payload["project_root"],
            operations=[Operation.from_dict(item) for item in payload.get("operations", [])],
            warnings=list(payload.get("warnings", [])),
            errors=list(payload.get("errors", [])),
            rule=payload.get("rule", "manual"),
            created_at=payload.get("created_at", ""),
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > _HASH_LIMIT:
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def tree_digest(root: Path) -> tuple[str | None, int, int]:
    """Digest a whole folder: (hash, file_count, total_bytes).

    Used to prove a folder move was lossless, and to refuse rolling back a
    folder the user edited in the meantime.
    """
    entries: list[str] = []
    total = 0
    count = 0
    try:
        paths = sorted(root.rglob("*"))
    except OSError:
        return None, 0, 0
    for path in paths:
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        digest = _sha256(path) if size <= _HASH_LIMIT else None
        entries.append(f"{rel}|{size}|{digest or ''}")
        total += size
        count += 1
    blob = "\n".join(entries).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), count, total


def normalize_rel(project: ProjectInfo, raw: str) -> str | None:
    """Accept `Assets/a.png`, `a.png`, `./Assets/a.png`; return a safe rel path."""
    text = raw.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    if not text:
        return None
    if text.startswith("Assets/"):
        candidate = text
    else:
        candidate = "Assets/" + text
    parts = candidate.split("/")
    if ".." in parts or candidate.startswith("/"):
        return None
    if any(part in ("", ".") for part in parts):
        return None
    if parts[0] != "Assets" or len(parts) < 2:
        return None
    return candidate


def _exists_case_insensitive(path: Path) -> bool:
    """Windows/macOS filesystems are case-insensitive: `Sword.png` and
    `sword.png` are the same slot, and that collision must be caught."""
    if path.exists():
        return True
    parent = path.parent
    if not parent.is_dir():
        return False
    target = path.name.lower()
    try:
        return any(child.name.lower() == target for child in parent.iterdir())
    except OSError:
        return False


def _name_problem(name: str) -> str | None:
    if ignored_by_unity(name):
        return "Unity 会忽略点开头或 ~ 结尾的名字"
    if _INVALID_CHARS.search(name):
        return "文件名含 Windows 非法字符"
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.upper() in _RESERVED:
        return "文件名是系统保留名"
    if name.endswith((" ", ".")):
        return "文件名以空格或点结尾"
    return None


# --------------------------------------------------------------------------- #
# plan construction
# --------------------------------------------------------------------------- #
def _in_special_folder(rel: str, name: str) -> bool:
    return name in rel.split("/")[:-1]


def _special_folder_warning(from_rel: str, to_rel: str) -> str | None:
    """Resources/StreamingAssets are addressed by *string path* at runtime, so
    moving a file out of them breaks loading without breaking any GUID."""
    for folder in ("Resources", "StreamingAssets"):
        if _in_special_folder(from_rel, folder) and not _in_special_folder(to_rel, folder):
            return (f"{from_rel} 在 {folder}/ 里：移出去会让按字符串路径加载的代码失效"
                    f"（Resources.Load / 路径拼接），而且 Unity 不会报错。")
    return None


def build_plan(project: ProjectInfo, pairs: list[tuple[str, str]],
               rule: str = "manual") -> Plan:
    """`pairs` are (from, to) paths; hashes and .meta pairing are resolved here."""
    plan = Plan(project_root=str(project.root), rule=rule)
    seen_from: set[str] = set()
    seen_to: dict[str, str] = {}
    root = project.root

    # A↔B swaps are NOT rejected any more: a destination that another operation
    # is about to vacate counts as free, and the ordering pass inserts temporary
    # names when a cycle cannot be linearised.
    normalized = [(normalize_rel(project, a), normalize_rel(project, b)) for a, b in pairs]
    declared_sources = {source.lower() for source, _ in normalized if source}

    for index, (raw_from, raw_to) in enumerate(pairs, start=1):
        from_rel = normalize_rel(project, raw_from)
        to_rel = normalize_rel(project, raw_to)
        if from_rel is None:
            plan.errors.append(f"源路径不合法：{raw_from!r}（必须位于 Assets/ 下）")
            continue
        if to_rel is None:
            plan.errors.append(f"目标路径不合法：{raw_to!r}（必须位于 Assets/ 下）")
            continue
        if from_rel in seen_from:
            plan.errors.append(f"同一个源被规划了两次：{from_rel}")
            continue
        if to_rel.lower() in seen_to:
            plan.errors.append(f"两个操作争用同一个目标：{to_rel}")
            continue
        seen_from.add(from_rel)
        seen_to[to_rel.lower()] = from_rel

        src = root / from_rel
        dst = root / to_rel
        if not src.exists():
            plan.errors.append(f"源不存在：{from_rel}")
            continue

        src_is_dir = src.is_dir()
        if src_is_dir:
            lowered_from, lowered_to = from_rel.lower(), to_rel.lower()
            # Case-only renames have equal lowercase paths and are legitimate.
            if lowered_to != lowered_from and lowered_to.startswith(lowered_from + "/"):
                plan.errors.append(f"不能把文件夹移动到它自己里面：{from_rel} → {to_rel}")
                continue
        problem = _name_problem(dst.name)
        if problem:
            plan.errors.append(f"目标名不可用（{problem}）：{to_rel}")
            continue

        same_slot = from_rel.lower() == to_rel.lower()
        warning = _special_folder_warning(from_rel, to_rel)
        if warning:
            plan.warnings.append(warning)
        # A destination that another planned operation is vacating is fine:
        # that is exactly the a↔b swap case.
        target_is_being_vacated = to_rel.lower() in declared_sources
        if not same_slot and not target_is_being_vacated and _exists_case_insensitive(dst):
            plan.errors.append(f"目标已存在，绝不覆盖：{to_rel}")
            continue
        if not same_slot and not target_is_being_vacated and dst.exists():
            plan.errors.append(f"目标已存在，绝不覆盖：{to_rel}")
            continue

        if src_is_dir:
            kind = "move_dir"
        elif src.parent == dst.parent or from_rel.lower() == to_rel.lower():
            kind = "rename"
        else:
            kind = "move"
        op = Operation(
            op_id=index,
            kind=kind,
            from_rel=from_rel,
            to_rel=to_rel,
            size=src.stat().st_size if not src_is_dir else 0,
            mtime_ns=src.stat().st_mtime_ns,
            sha256=None if src_is_dir else _sha256(src),
            reason=rule,
            is_dir=src_is_dir,
        )
        if src_is_dir:
            op.tree_hash, op.file_count, op.size = tree_digest(src)
            if op.file_count == 0:
                plan.warnings.append(f"{from_rel} 是空文件夹（Unity 会忽略空目录）")

        meta_src = Path(str(src) + ".meta")
        if meta_src.is_file():
            op.from_meta_rel = from_rel + ".meta"
            op.to_meta_rel = to_rel + ".meta"
            op.meta_sha256 = _sha256(meta_src)
            parsed = parse_meta(meta_src, op.from_meta_rel)
            op.guid = parsed.guid
            if op.guid is None:
                plan.warnings.append(f"{from_rel} 的 .meta 没有 guid，移动后将无法保持引用")
        elif src_is_dir:
            plan.warnings.append(
                f"{from_rel} 没有目录 .meta：Unity 会新建一个（新 GUID），"
                "如果有资源引用这个目录本身，引用会断。"
            )
        else:
            plan.warnings.append(
                f"{from_rel} 没有 .meta：Unity 会为它分配新 GUID，"
                "原有引用会断开（免费版不阻止，但请确认）。"
            )
        plan.operations.append(op)

    _check_overlaps(plan)
    if not plan.errors:
        plan.operations = _order_operations(plan.operations)
    return plan


def _temp_rel(from_rel: str, salt: str) -> str:
    path = PurePosixPath(from_rel)
    return str(path.with_name(f"{path.stem}.ugswap-{salt}{path.suffix}"))


def _order_operations(operations: list[Operation]) -> list[Operation]:
    """Order operations so a destination is free before it is used.

    Handles the two cases a naive order gets wrong:

    * **chains** (`b→c` then `a→b`): the operation vacating the target must run
      first - a topological sort fixes this with no temporary file at all;
    * **cycles** (`a→b`, `b→a`): no order exists, so one stuck member is parked
      under a temporary name first. Only genuinely stuck operations pay that
      cost, not every rename in the plan.
    """
    remaining = list(operations)
    ordered: list[Operation] = []
    hops = 0
    guard = 0
    limit = len(operations) * 4 + 8

    while remaining and guard <= limit:
        guard += 1
        sources = {op.from_rel.lower() for op in remaining}
        ready = [op for op in remaining if op.to_rel.lower() not in sources]
        if ready:
            op = ready[0]
            ordered.append(op)
            remaining.remove(op)
            continue

        victim = remaining[0]
        hops += 1
        temp_rel = _temp_rel(victim.from_rel, f"{hops:02d}")
        ordered.append(Operation(
            op_id=0,
            kind="rename",
            from_rel=victim.from_rel,
            to_rel=temp_rel,
            from_meta_rel=victim.from_meta_rel,
            to_meta_rel=(temp_rel + ".meta") if victim.from_meta_rel else None,
            size=victim.size,
            mtime_ns=victim.mtime_ns,
            sha256=victim.sha256,
            meta_sha256=victim.meta_sha256,
            guid=victim.guid,
            reason="swap",
            is_dir=victim.is_dir,
            tree_hash=victim.tree_hash,
            file_count=victim.file_count,
            is_temp=True,
        ))
        victim.from_rel = temp_rel
        if victim.from_meta_rel:
            victim.from_meta_rel = temp_rel + ".meta"

    for index, op in enumerate(ordered, start=1):
        op.op_id = index
    return ordered


def _check_overlaps(plan: Plan) -> None:
    """A folder move and a file move inside that folder cannot both run."""
    dir_ops = [op for op in plan.operations if op.is_dir]
    for folder in dir_ops:
        prefix = folder.from_rel.lower() + "/"
        for other in plan.operations:
            if other is folder:
                continue
            if other.from_rel.lower().startswith(prefix):
                if other.op_id > folder.op_id:
                    plan.errors.append(
                        f"操作重叠：{other.from_rel} 已经包含在文件夹操作 "
                        f"{folder.from_rel} 里，不能重复规划。")
            if other.to_rel.lower().startswith(prefix):
                plan.errors.append(
                    f"目标冲突：{other.to_rel} 落在被移动的文件夹 {folder.from_rel} 内部。")


def build_plan_from_json(project: ProjectInfo, payload: dict) -> Plan:
    """Accept {"moves": [{"from": "...", "to": "..."}]} or {"moves": [["a","b"]]}."""
    entries = payload.get("moves") or payload.get("renames") or payload.get("operations") or []
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            source = entry.get("from") or entry.get("src") or entry.get("path")
            target = entry.get("to") or entry.get("dst") or entry.get("new_path")
            if not source or not target:
                continue
            pairs.append((str(source), str(target)))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            pairs.append((str(entry[0]), str(entry[1])))
    return build_plan(project, pairs, rule=payload.get("rule", "manual"))


def build_category_plan(project: ProjectInfo, assets, *, root_folder: str = "Organized") -> Plan:
    """Auto-organise rule: sort assets into Assets/<root>/<category>/<sub>/.

    This is the "one click tidy up a bought asset pack" feature.
    """
    pairs: list[tuple[str, str]] = []
    for asset in assets:
        if asset.category in ("script", "data", "other"):
            continue
        # Never auto-move these: they are loaded by string path, not by GUID.
        if asset.is_in_resources or asset.is_in_streaming_assets:
            continue
        parts = asset.rel.split("/")
        if len(parts) > 2 and parts[1] == root_folder:
            continue
        target_dir = f"Assets/{root_folder}/{asset.category}"
        pairs.append((asset.rel, f"{target_dir}/{asset.path.name}"))
    plan = build_plan(project, pairs, rule="category")
    return plan


def save_plan(plan: Plan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_plan(path: Path) -> Plan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Plan.from_dict(payload)


def verify_sources(project: ProjectInfo, plan: Plan, *, force: bool = False) -> list[str]:
    """Re-check every source right before touching it (TOCTOU guard)."""
    problems: list[str] = []
    synthetic_sources = {op.to_rel.lower() for op in plan.operations if op.is_temp}
    # Destinations another operation is about to vacate (a↔b swaps) are fine.
    vacating = {op.from_rel.lower() for op in plan.operations}
    for op in plan.operations:
        # Sources that only exist mid-plan (a swap's temporary hop) cannot be
        # verified up front; the hop that creates them is verified instead.
        if op.is_temp or op.from_rel.lower() in synthetic_sources:
            continue
        src = project.root / op.from_rel
        dst = project.root / op.to_rel
        if op.kind == "meta_edit":
            meta_src = project.root / (op.from_meta_rel or "")
            if not meta_src.is_file():
                problems.append(f"缺少 .meta：{op.from_meta_rel}")
                continue
            current = _sha256(meta_src)
            if op.meta_sha256 and current != op.meta_sha256:
                problems.append(f".meta 内容已变化：{op.from_meta_rel}")
            continue
        if not src.exists():
            problems.append(f"源已消失：{op.from_rel}")
            continue
        if op.is_dir:
            if not src.is_dir():
                problems.append(f"源已不是文件夹：{op.from_rel}")
                continue
            if op.tree_hash:
                current, _, _ = tree_digest(src)
                if current != op.tree_hash:
                    problems.append(f"文件夹内容已变化：{op.from_rel}")
        else:
            stat = src.stat()
            if stat.st_size != op.size:
                problems.append(f"源文件大小已变化：{op.from_rel}")
            elif op.sha256 and _sha256(src) != op.sha256:
                problems.append(f"源文件内容已变化：{op.from_rel}")
        if (not force and op.to_rel.lower() not in vacating
                and _exists_case_insensitive(dst)
                and os.path.normcase(str(dst)) != os.path.normcase(str(src))):
            problems.append(f"目标已被占用：{op.to_rel}")
        meta_src = Path(str(src) + ".meta")
        if op.from_meta_rel and not meta_src.is_file():
            problems.append(f"源 .meta 已消失：{op.from_meta_rel}")
        elif op.meta_sha256 and _sha256(meta_src) != op.meta_sha256:
            problems.append(f".meta 内容已变化：{op.from_meta_rel}")
    return problems


def plan_table(plan: Plan, limit: int = 200) -> list[tuple[str, str, str]]:
    rows = []
    for op in plan.operations[:limit]:
        rows.append((op.kind, op.from_rel, op.to_rel))
    return rows
