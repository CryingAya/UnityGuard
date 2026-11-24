"""One-click importer fixes: rewrite `.meta` settings that the audit flagged.

Why this is the money feature: a normal map imported as a *colour* texture, a
2 GB of PNGs marked Read/Write, a 12 s BGM decoded into RAM - each one costs
frame time or build size, and none of them break loudly. Fixing them means
editing `.meta` files *without* touching the guid line, which is exactly what
this module does (in-place text edit, formatting preserved, backup for rollback).

Nothing here touches the asset itself - only the small sidecar file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .media import guess_texture_purpose, wav_info
from .metainfo import MetaFile, parse_meta_text
from .model import Asset
from .plan import Operation, Plan
from .project import ProjectInfo

_KEY_LINE = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*):[ \t]*(?P<value>.*?)[ \t]*(?P<eol>\r?\n)?$")
_IMPORTER_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*$")

_LINEAR_PURPOSES = {"normal", "roughness", "metallic", "ao", "orm", "mask", "height"}
_UI_PURPOSES = {"ui"}


@dataclass
class Edit:
    key: str
    old: str
    new: str
    reason: str

    def to_dict(self) -> dict:
        return {"key": self.key, "old": self.old, "new": self.new, "reason": self.reason}

    @classmethod
    def from_dict(cls, payload: dict) -> "Edit":
        return cls(payload["key"], payload["old"], payload["new"],
                   payload.get("reason", ""))


@dataclass
class FixProposal:
    asset: Asset
    meta: MetaFile
    edits: list[Edit] = field(default_factory=list)


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def read_meta_text(path: Path) -> str:
    """Read without newline translation: CRLF files must stay CRLF.

    `Path.read_text()` would rewrite every `\\r\\n` to `\\n`, which changes bytes
    on lines we never meant to touch (and breaks byte-level rollback).
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _int_prop(meta: MetaFile, *keys: str) -> int | None:
    for key in keys:
        value = meta.get_int(key)
        if value is not None:
            return value
    return None


# --------------------------------------------------------------------------- #
# what to fix
# --------------------------------------------------------------------------- #
def propose_edits(asset: Asset, meta: MetaFile, *,
                  cap_texture_size: int | None = None) -> list[Edit]:
    if meta.importer == "TextureImporter" or any(
            key.startswith("TextureImporter.") for key in meta.props):
        return _texture_edits(asset, meta, cap_texture_size)
    if meta.importer == "AudioImporter" or any(
            key.startswith("AudioImporter.") for key in meta.props):
        return _audio_edits(asset, meta)
    if meta.importer == "ModelImporter" or any(
            key.startswith("ModelImporter.") for key in meta.props):
        return _model_edits(asset, meta)
    return []


def _texture_edits(asset: Asset, meta: MetaFile, cap: int | None) -> list[Edit]:
    edits: list[Edit] = []
    purpose = guess_texture_purpose(asset.stem)
    srgb = _int_prop(meta, "TextureImporter.mipmaps.sRGBTexture", "TextureImporter.sRGBTexture")
    tex_type = _int_prop(meta, "TextureImporter.textureType")
    readable = _int_prop(meta, "TextureImporter.isReadable")
    max_size = _int_prop(meta, "TextureImporter.maxTextureSize")

    if purpose == "normal":
        if tex_type is not None and tex_type != 1:
            edits.append(Edit("textureType", str(tex_type), "1",
                              "法线图必须是 NormalMap 类型"))
        if srgb == 1:
            edits.append(Edit("sRGBTexture", "1", "0",
                              "法线是线性数据，勾 sRGB 会让光照发脏"))
    elif purpose in _LINEAR_PURPOSES and srgb == 1:
        edits.append(Edit("sRGBTexture", "1", "0",
                          f"{purpose} 属于线性数据，不能按 sRGB 采样"))

    if readable == 1 and purpose not in _UI_PURPOSES:
        edits.append(Edit("isReadable", "1", "0",
                          "Read/Write 会常驻一份 CPU 副本，贴图多时内存翻倍"))

    if cap and purpose not in _UI_PURPOSES and max_size and max_size > cap:
        edits.append(Edit("maxTextureSize", str(max_size), str(cap),
                          f"超过 {cap} 的贴图在移动端/低配机上代价很高"))
    return edits


def _audio_edits(asset: Asset, meta: MetaFile) -> list[Edit]:
    edits: list[Edit] = []
    load_type = _int_prop(meta, "AudioImporter.defaultSettings.loadType",
                          "AudioImporter.loadType")
    compression = _int_prop(meta, "AudioImporter.defaultSettings.compressionFormat",
                            "AudioImporter.compressionFormat")
    duration = None
    if asset.ext == ".wav":
        info = wav_info(asset.path)
        if info:
            duration = float(info["duration"])

    if duration is not None and duration >= 10:
        if compression == 0:
            edits.append(Edit("compressionFormat", "0", "1",
                              f"{duration:.0f}s 长音频用未压缩 PCM，包体浪费最严重"))
        if load_type == 0:
            edits.append(Edit("loadType", "0", "1",
                              "长音频 DecompressOnLoad 会整段解码进内存"))
    elif duration is not None and duration < 1.5 and load_type == 2:
        edits.append(Edit("loadType", "2", "0",
                          "短音效 Streaming 每次播放都要走磁盘，会有延迟"))
    return edits


def _model_edits(asset: Asset, meta: MetaFile) -> list[Edit]:
    readable = _int_prop(meta, "ModelImporter.isReadable",
                         "ModelImporter.animations.isReadable",
                         "ModelImporter.meshes.isReadable")
    if readable == 1:
        return [Edit("isReadable", "1", "0",
                     "模型 Read/Write 会保留网格的 CPU 副本")]
    return []


def proposals(project: ProjectInfo, assets: list[Asset], index, *,
              cap_texture_size: int | None = None) -> list[FixProposal]:
    out: list[FixProposal] = []
    for asset in assets:
        meta = index.by_rel.get(asset.meta_rel or "")
        if meta is None or not meta.guid:
            continue
        edits = propose_edits(asset, meta, cap_texture_size=cap_texture_size)
        if edits:
            out.append(FixProposal(asset=asset, meta=meta, edits=edits))
    return out


# --------------------------------------------------------------------------- #
# text editing
# --------------------------------------------------------------------------- #
def _importer_block(lines: list[str]) -> tuple[int, int]:
    """(start, end) line indexes of the importer block, or (0, len)."""
    start = -1
    for index, line in enumerate(lines):
        if _IMPORTER_LINE.match(line.rstrip("\r\n")):
            start = index
            break
    if start < 0:
        return 0, len(lines)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].rstrip("\r\n")
        if not stripped.strip():
            continue
        if not stripped[0].isspace():
            end = index
            break
    return start, end


def edit_meta_text(text: str, edits: list[Edit]) -> tuple[str, list[Edit], list[str]]:
    """Rewrite values in place, keeping indentation, order and line endings."""
    lines = text.splitlines(keepends=True)
    start, end = _importer_block(lines)
    applied: list[Edit] = []
    problems: list[str] = []

    for edit in edits:
        hit = None
        for index in range(start, end):
            match = _KEY_LINE.match(lines[index])
            if match and match.group("key") == edit.key:
                hit = (index, match)
                break
        if hit is None:
            problems.append(f"找不到键 {edit.key}")
            continue
        index, match = hit
        current = match.group("value").strip()
        if current != edit.old:
            problems.append(f"{edit.key} 的当前值是 {current!r}，与计划假设的 {edit.old!r} 不符")
            continue
        eol = match.group("eol") or ""
        lines[index] = f"{match.group('indent')}{edit.key}: {edit.new}{eol}"
        applied.append(edit)

    return "".join(lines), applied, problems


def preview(asset: Asset, meta: MetaFile, edits: list[Edit]) -> str:
    return f"{asset.rel}  →  " + ", ".join(f"{edit.key}: {edit.old}→{edit.new}" for edit in edits)


# --------------------------------------------------------------------------- #
# plan integration
# --------------------------------------------------------------------------- #
def build_fix_plan(project: ProjectInfo, assets: list[Asset], index, *,
                   cap_texture_size: int | None = None,
                   rule: str = "fix") -> Plan:
    plan = Plan(project_root=str(project.root), rule=rule)
    op_id = 0
    for proposal in proposals(project, assets, index, cap_texture_size=cap_texture_size):
        meta_path = project.root / proposal.meta.rel
        try:
            original = read_meta_text(meta_path)
        except OSError as exc:
            plan.errors.append(f"读不到 {proposal.meta.rel}：{exc}")
            continue

        new_text, applied, problems = edit_meta_text(original, proposal.edits)
        if problems or not applied:
            for problem in problems:
                plan.errors.append(f"{proposal.meta.rel}：{problem}")
            continue

        # The guid must survive the edit; otherwise every reference breaks.
        guid_before = parse_meta_text(original, proposal.meta.rel).guid
        guid_after = parse_meta_text(new_text, proposal.meta.rel).guid
        if guid_before != guid_after:
            plan.errors.append(f"{proposal.meta.rel}：编辑后 GUID 变化，已放弃该修复")
            continue

        op_id += 1
        plan.operations.append(Operation(
            op_id=op_id,
            kind="meta_edit",
            from_rel=proposal.asset.rel,
            to_rel=proposal.asset.rel,
            from_meta_rel=proposal.meta.rel,
            to_meta_rel=proposal.meta.rel,
            meta_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
            new_meta_sha256=hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
            guid=guid_before,
            reason=rule,
            edits=[edit.to_dict() for edit in applied],
        ))
    return plan


def fix_issues_summary(plan: Plan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for op in plan.operations:
        for edit in op.edits:
            counts[edit["reason"]] = counts.get(edit["reason"], 0) + 1
    return counts
