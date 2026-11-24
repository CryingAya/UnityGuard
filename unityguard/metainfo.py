"""Unity .meta parsing: GUID index, duplicate / orphan / missing detection.

The single most important fact this module encodes: Unity references assets by
GUID (the `guid:` line inside the .meta), never by file path. A .meta that goes
missing silently re-creates its asset under a *new* GUID, which is the real
cause of "my scene turned pink / my prefab lost its mesh".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import Asset, MetaFile
from .project import ProjectInfo

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*|-[A-Za-z0-9_.\-]+):[ \t]*(.*)$")
_GUID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class MetaIndex:
    metas: list[MetaFile] = field(default_factory=list)
    by_guid: dict[str, list[MetaFile]] = field(default_factory=dict)
    by_rel: dict[str, MetaFile] = field(default_factory=dict)
    duplicate_guids: dict[str, list[MetaFile]] = field(default_factory=dict)
    orphan_metas: list[MetaFile] = field(default_factory=list)
    metas_without_guid: list[MetaFile] = field(default_factory=list)
    assets_missing_meta: list[Asset] = field(default_factory=list)
    folder_metas: int = 0

    def guid_of(self, rel: str) -> str | None:
        meta = self.by_rel.get(rel + ".meta")
        return meta.guid if meta else None


def parse_meta(path: Path, rel: str) -> MetaFile:
    """Parse a Unity .meta just deep enough to answer: guid + importer props."""
    meta = MetaFile(path=path, rel=rel, guid=None, importer=None)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return meta
    return parse_meta_text(text, rel, path)


def parse_meta_text(text: str, rel: str, path: Path | None = None) -> MetaFile:
    """Same as parse_meta, but from a string (used to verify an edit in memory)."""
    meta = MetaFile(path=path or Path(rel), rel=rel, guid=None, importer=None)
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.lstrip(" ")
        if stripped.startswith("- "):
            continue
        indent = len(raw) - len(stripped)
        match = _KEY_RE.match(stripped)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if indent == 0 and key == "guid":
            meta.guid = value.lower()
        elif indent == 0 and value == "" and key[:1].isupper():
            meta.importer = key
        dotted = ".".join([name for _, name in stack] + [key])
        meta.props.setdefault(dotted, value)
        if value == "":
            stack.append((indent, key))
    return meta


def build_index(info: ProjectInfo, assets: list[Asset]) -> MetaIndex:
    index = MetaIndex()
    asset_rels = {asset.rel for asset in assets}
    folder_rels: set[str] = set()

    for path in _walk_metas(info):
        rel = info.rel(path)
        meta = parse_meta(path, rel)
        companion = rel[: -len(".meta")]
        meta.companion_rel = companion
        index.metas.append(meta)
        index.by_rel[rel] = meta
        if meta.guid is None:
            index.metas_without_guid.append(meta)
        elif not _GUID_RE.match(meta.guid):
            index.metas_without_guid.append(meta)
        else:
            index.by_guid.setdefault(meta.guid, []).append(meta)

        if companion not in asset_rels:
            # Folders also carry a .meta, and that is legal.
            if (info.root / companion).is_dir():
                index.folder_metas += 1
                folder_rels.add(companion)
            else:
                index.orphan_metas.append(meta)

    for guid, metas in index.by_guid.items():
        if len(metas) > 1:
            index.duplicate_guids[guid] = metas

    for asset in assets:
        meta_rel = asset.rel + ".meta"
        meta = index.by_rel.get(meta_rel)
        if meta is not None:
            asset.has_meta = True
            asset.meta_rel = meta_rel
            if meta.guid:
                asset.guid = meta.guid
        elif asset.is_in_streaming_assets:
            # StreamingAssets is copied verbatim; .meta is optional-ish but
            # Unity still wants one. Do not hard-fail it.
            pass
        else:
            index.assets_missing_meta.append(asset)

    return index


def _walk_metas(info: ProjectInfo):
    from .project import walk_metas

    return walk_metas(info)


def importer_props(meta: MetaFile | None, *keys: str) -> dict[str, str]:
    """Read dotted importer keys, e.g. ('mipmaps.enableMipMap',)."""
    out: dict[str, str] = {}
    if meta is None:
        return out
    for key in keys:
        value = meta.props.get(key)
        if value is not None:
            out[key] = value
    return out
