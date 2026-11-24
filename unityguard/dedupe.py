"""Content de-duplication: hide unused duplicate copies where Unity cannot see them.

Moving a duplicate to `Assets/_Duplicates~/` is the safest possible "delete":

* the folder ends with `~`, so **Unity ignores it entirely** - no import, no build
  size, no GUID conflicts;
* the bytes are still on disk, so the user can inspect them before really deleting;
* the operation is a normal move with a `.meta` pair, so rollback is byte-exact.

Only duplicates that the reference graph proves unreachable are quarantined. A
duplicate that is still referenced is never touched - it is a *different asset*
with a different GUID, and merging them is a decision only the user can make.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from .i18n import tr
from .model import Asset
from .plan import Operation, Plan
from .scan import Scan

QUARANTINE_DIR = "Assets/_Duplicates~"

# Assets addressed by string path (or loaded dynamically) are never quarantined.
_SKIP_CATEGORIES = {"script", "shader", "engine", "data"}
_SKIP_EXTS = {".dll", ".asmdef", ".asmref"}


@dataclass
class DuplicateGroup:
    digest: str
    size: int
    keep: Asset | None
    hide: list[Asset]


def _sha256(path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def find_groups(scan_result: Scan) -> list[DuplicateGroup]:
    by_size: dict[int, list[Asset]] = {}
    for asset in scan_result.assets:
        if asset.size <= 0 or asset.category in _SKIP_CATEGORIES:
            continue
        if asset.ext in _SKIP_EXTS:
            continue
        if asset.is_in_resources or asset.is_in_streaming_assets:
            continue
        by_size.setdefault(asset.size, []).append(asset)

    groups: list[DuplicateGroup] = []
    for candidates in by_size.values():
        if len(candidates) < 2:
            continue
        by_hash: dict[str, list[Asset]] = {}
        for asset in candidates:
            digest = _sha256(asset.path)
            if digest:
                by_hash.setdefault(digest, []).append(asset)
        for digest, copies in by_hash.items():
            if len(copies) < 2:
                continue
            reachable = [asset for asset in copies
                         if asset.guid and asset.guid in scan_result.graph.reachable]
            if reachable:
                keep = sorted(reachable, key=lambda asset: (len(asset.rel), asset.rel))[0]
            else:
                keep = sorted(copies, key=lambda asset: (len(asset.rel), asset.rel))[0]
            hide = [asset for asset in copies if asset is not keep]
            groups.append(DuplicateGroup(digest=digest, size=keep.size,
                                         keep=keep, hide=hide))
    groups.sort(key=lambda group: -group.size * len(group.hide))
    return groups


def _target_rel(asset: Asset) -> str:
    path = PurePosixPath(asset.rel)
    # Keep the original structure so two same-named files do not collide.
    inner = path.relative_to("Assets")
    return f"{QUARANTINE_DIR}/{inner.as_posix()}"


def build_dedupe_plan(scan_result: Scan, *, rule: str = "dedupe") -> Plan:
    plan = Plan(project_root=str(scan_result.info.root), rule=rule)
    groups = find_groups(scan_result)
    if not groups:
        return plan

    op_id = 0
    for group in groups:
        if not group.keep:
            continue
        plan.warnings.append(tr(
            "{n} 份完全相同（{size}），保留 {keep}",
            "{n} identical copies ({size}), keeping {keep}",
            n=len(group.hide) + 1, size=_human(group.size), keep=group.keep.rel,
        ))
        for asset in group.hide:
            op_id += 1
            meta_rel = asset.rel + ".meta"
            plan.operations.append(Operation(
                op_id=op_id,
                kind="move",
                from_rel=asset.rel,
                to_rel=_target_rel(asset),
                from_meta_rel=meta_rel if asset.has_meta else None,
                to_meta_rel=(_target_rel(asset) + ".meta") if asset.has_meta else None,
                size=asset.size,
                mtime_ns=int(asset.path.stat().st_mtime_ns),
                sha256=_sha256(asset.path),
                guid=asset.guid,
                reason=rule,
            ))
    plan.warnings.append(tr(
        "被移走的文件在 Assets/_Duplicates~/ 里，Unity 不会导入这个目录；"
        "确认无用后可以手动删除整个目录。",
        "Quarantined files live in Assets/_Duplicates~/ - Unity ignores that folder entirely. "
        "Delete the folder by hand once you are sure.",
    ))
    return plan


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
