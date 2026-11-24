"""Reference graph: which GUIDs are referenced, and what is actually reachable.

This is the part generic renamers do not have. Unity stores references as GUIDs
inside YAML assets, so "is this file used?" is a graph reachability question,
not a filename question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import Asset
from .metainfo import MetaIndex
from .project import MAX_REF_SCAN_BYTES, YAML_REF_EXTS, ProjectInfo

_GUID_RE = re.compile(r"guid:\s*([0-9a-fA-F]{32})")
_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.){1,200})"')
_BUILTIN_PREFIX = "0000000000000000"


@dataclass
class RefGraph:
    out_refs: dict[str, set[str]] = field(default_factory=dict)        # asset guid -> guids it points at
    in_refs: dict[str, dict[str, int]] = field(default_factory=dict)   # guid -> {src guid: count}
    root_guids: set[str] = field(default_factory=set)
    reachable: set[str] = field(default_factory=set)
    missing_targets: dict[str, set[str]] = field(default_factory=dict)  # src rel -> dangling guids
    scanned_files: int = 0
    code_strings: dict[str, set[str]] = field(default_factory=dict)     # token -> .cs rels
    # Assets that *should* be YAML but are serialized binary (Force Text off).
    # On such projects the reference graph is unusable, so we must not claim
    # that anything is unused.
    binary_assets: list[str] = field(default_factory=list)

    @property
    def reference_analysis_usable(self) -> bool:
        return not self.binary_assets

    def referenced_by(self, guid: str) -> dict[str, int]:
        return self.in_refs.get(guid, {})


def read_text_if_yaml(path: Path) -> str | None:
    """Return YAML text, or None for binary / oversized / unreadable files."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0 or size > MAX_REF_SCAN_BYTES:
        return None
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
            if b"\x00" in head:
                return None
            rest = handle.read()
    except OSError:
        return None
    data = head + rest
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _collect_code_strings(info: ProjectInfo, assets: list[Asset]) -> dict[str, set[str]]:
    tokens: dict[str, set[str]] = {}
    for asset in assets:
        if asset.ext not in (".cs", ".js"):
            continue
        text = read_text_if_yaml(asset.path)
        if not text:
            continue
        for literal in _STRING_RE.findall(text):
            if len(literal) < 3:
                continue
            lowered = literal.replace("\\\\", "/").lstrip("@").lower()
            tokens.setdefault(lowered, set()).add(asset.rel)
            tail = lowered.rsplit("/", 1)[-1]
            if tail:
                tokens.setdefault(tail, set()).add(asset.rel)
    return tokens


def build_graph(info: ProjectInfo, assets: list[Asset], index: MetaIndex) -> RefGraph:
    graph = RefGraph()

    def scan_source(text: str, source_guid: str | None, source_rel: str) -> None:
        if not text:
            return
        graph.scanned_files += 1
        found = set(_GUID_RE.findall(text))
        if not found:
            return
        # Unity built-in resources (default skybox, spot cookie, ...) use GUIDs
        # that start with 16 zeros; they never exist as project assets.
        lowered = {guid.lower() for guid in found}
        lowered = {guid for guid in lowered
                   if not guid.startswith(_BUILTIN_PREFIX)}
        if not lowered:
            return
        if source_guid:
            lowered.discard(source_guid)
        if source_guid:
            outgoing = graph.out_refs.setdefault(source_guid, set())
            outgoing |= lowered
        for target in lowered:
            slot = graph.in_refs.setdefault(target, {})
            key = source_guid or f"@{source_rel}"
            slot[key] = slot.get(key, 0) + 1
        dangling = {guid for guid in lowered if guid not in index.by_guid}
        if dangling:
            graph.missing_targets.setdefault(source_rel, set()).update(dangling)

    for asset in assets:
        if asset.ext not in YAML_REF_EXTS:
            continue
        text = read_text_if_yaml(asset.path)
        if text is None:
            if asset.size > 0:
                # Unity 5.x exported binary scenes/prefabs: readable only by the
                # editor's serialized-file parser, not by us.
                graph.binary_assets.append(asset.rel)
            continue
        scan_source(text, asset.guid, asset.rel)

    # ProjectSettings assets are always in the build -> they are roots.
    for settings_file in sorted((info.root / "ProjectSettings").glob("*.asset")):
        text = read_text_if_yaml(settings_file)
        if not text:
            continue
        scan_source(text, None, info.rel(settings_file))
        graph.root_guids.update(
            guid.lower() for guid in _GUID_RE.findall(text)
            if not guid.lower().startswith(_BUILTIN_PREFIX)
        )

    # Resources/ is loaded by string path at runtime -> implicitly used.
    for asset in assets:
        if asset.is_in_resources and asset.guid:
            graph.root_guids.add(asset.guid)

    # Addressables: settings asset is an entry point, groups hang off it.
    addressables = info.assets_dir / "AddressableAssetsData"
    if addressables.is_dir():
        for path in addressables.rglob("*.asset"):
            text = read_text_if_yaml(path)
            if not text:
                continue
            for guid in _GUID_RE.findall(text):
                if not guid.lower().startswith(_BUILTIN_PREFIX):
                    graph.root_guids.add(guid.lower())

    # BFS from roots along outgoing references.
    queue = list(graph.root_guids)
    seen: set[str] = set()
    while queue:
        guid = queue.pop()
        if guid in seen:
            continue
        seen.add(guid)
        for target in graph.out_refs.get(guid, ()):  # type: ignore[arg-type]
            if target not in seen:
                queue.append(target)
    graph.reachable = seen
    graph.code_strings = _collect_code_strings(info, assets)
    return graph


def code_string_hits(graph: RefGraph, asset: Asset) -> list[str]:
    """Return .cs files whose string literals mention this asset's name."""
    stem = asset.stem.lower()
    name = asset.name.lower()
    rel = asset.rel.lower()
    hits: set[str] = set()
    for token in (stem, name, rel, rel.removeprefix("assets/")):
        hits |= graph.code_strings.get(token, set())
    return sorted(hits)
