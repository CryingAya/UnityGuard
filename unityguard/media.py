"""Media header probes + importer-setting cross-checks.

We only read a few bytes of each file (header), never decode the whole image or
audio stream, so this stays fast on big projects.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from .i18n import tr
from .model import Asset, Issue, MetaFile
from .metainfo import MetaIndex
from .project import ProjectInfo

_LINEAR_PURPOSES = {"normal", "roughness", "metallic", "ao", "orm", "mask", "height"}

_PURPOSE_TOKENS = {
    "normal": ("normal", "normalmap", "nrm", "nor"),
    "albedo": ("albedo", "basecolor", "basecolour", "diffuse", "diff"),
    "roughness": ("roughness", "rough", "rgh"),
    "metallic": ("metallic", "metalness", "metal", "mtl"),
    "ao": ("ao", "occlusion", "ambientocclusion"),
    "orm": ("orm", "ormp", "packed"),
    "mask": ("mask", "maskmap"),
    "height": ("height", "displacement", "disp", "bump"),
    "emission": ("emissive", "emission", "emiss"),
    "ui": ("ui", "icon", "button", "panel", "hud", "sprite"),
}

_TEX_TYPE_NAMES = {
    0: "Default", 1: "NormalMap", 2: "GUI", 3: "Sprite",
    4: "Cursor", 5: "Cookie", 6: "Lightmap", 7: "SingleChannel",
}
_LOAD_TYPE_NAMES = {0: "DecompressOnLoad", 1: "CompressedInMemory", 2: "Streaming"}
_COMPRESSION_NAMES = {0: "PCM", 1: "Vorbis", 2: "ADPCM"}


def _meta_int(meta: MetaFile | None, *keys: str) -> int | None:
    """Unity moves keys between versions (sRGBTexture sits under `mipmaps` in
    modern versions, at the top level in older ones), so try in order."""
    if meta is None:
        return None
    for key in keys:
        value = meta.get_int(key)
        if value is not None:
            return value
    return None


def _tokens(stem: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)
    return [token.lower() for token in re.split(r"[^A-Za-z0-9]+", spaced) if token]


def guess_texture_purpose(stem: str) -> str | None:
    tokens = set(_tokens(stem))
    compact = "".join(_tokens(stem))
    for purpose, needles in _PURPOSE_TOKENS.items():
        if tokens & set(needles):
            return purpose
    # Multi-word suffixes arrive as separate tokens (T_Rock_BaseColor ->
    # t/rock/base/color), so also look for them inside the joined string.
    for purpose, needles in _PURPOSE_TOKENS.items():
        if any(needle in compact for needle in needles if len(needle) >= 5):
            return purpose
    return None


def image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                width, height = struct.unpack(">II", head[16:24])
                return int(width), int(height)
            if head[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    marker = handle.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    code = marker[1]
                    if code in (0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
                        continue
                    length_bytes = handle.read(2)
                    if len(length_bytes) < 2:
                        return None
                    length = struct.unpack(">H", length_bytes)[0]
                    if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        data = handle.read(5)
                        if len(data) < 5:
                            return None
                        height, width = struct.unpack(">HH", data[1:5])
                        return int(width), int(height)
                    handle.seek(length - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def tga_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(18)
        if len(header) < 18:
            return None
        width, height = struct.unpack("<HH", header[12:16])
        return int(width), int(height)
    except (OSError, struct.error):
        return None


def wav_info(path: Path) -> dict[str, float | int] | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return None
            handle.seek(12)
            channels = rate = bits = 0
            data_size = 0
            while True:
                chunk = handle.read(8)
                if len(chunk) < 8:
                    break
                chunk_id, size = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
                if chunk_id == b"fmt ":
                    fmt = handle.read(min(size, 16))
                    channels = struct.unpack("<H", fmt[2:4])[0]
                    rate = struct.unpack("<I", fmt[4:8])[0]
                    bits = struct.unpack("<H", fmt[14:16])[0]
                    if size > 16:
                        handle.seek(size - 16, 1)
                elif chunk_id == b"data":
                    data_size = size
                    break
                else:
                    handle.seek(size + (size & 1), 1)
        if not rate or not channels:
            return None
        duration = data_size / float(rate * channels * max(bits // 8, 1)) if data_size else 0.0
        return {"channels": channels, "rate": rate, "bits": bits,
                "data_bytes": data_size, "duration": round(duration, 2)}
    except (OSError, struct.error, ZeroDivisionError):
        return None


def check_textures(info: ProjectInfo, assets: list[Asset], index: MetaIndex) -> list[Issue]:
    issues: list[Issue] = []
    srgb_on_linear: list[str] = []
    wrong_type: list[str] = []
    readable: list[str] = []
    oversized: list[str] = []

    for asset in assets:
        if asset.category != "texture":
            continue
        meta = index.by_rel.get(asset.meta_rel or "")
        purpose = guess_texture_purpose(asset.stem)
        srgb = _meta_int(meta, "TextureImporter.mipmaps.sRGBTexture",
                         "TextureImporter.sRGBTexture")
        tex_type = _meta_int(meta, "TextureImporter.textureType")
        max_size = _meta_int(meta, "TextureImporter.maxTextureSize")
        is_readable = _meta_int(meta, "TextureImporter.isReadable")

        if purpose in _LINEAR_PURPOSES and srgb == 1:
            srgb_on_linear.append(asset.rel)
        if purpose == "normal" and tex_type is not None and tex_type != 1:
            wrong_type.append(
                f"{asset.rel} → textureType={_TEX_TYPE_NAMES.get(tex_type, tex_type)}"
            )
        if is_readable == 1 and purpose not in {"ui", "sprite"}:
            readable.append(asset.rel)

        if asset.ext in (".png", ".jpg", ".jpeg", ".tga"):
            dims = tga_dimensions(asset.path) if asset.ext == ".tga" else image_dimensions(asset.path)
            if dims:
                width, height = dims
                if max(width, height) > 2048 and purpose not in {"ui", "sprite"}:
                    oversized.append(f"{asset.rel} ({width}x{height})")
                if srgb == 1 or tex_type is not None:
                    pass

    if srgb_on_linear:
        issues.append(Issue(
            "critical", "tex.srgb_on_linear",
            tr(f"{len(srgb_on_linear)} 张数据贴图被标成了 sRGB", f"{len(srgb_on_linear)} data textures are marked as sRGB"),
            detail="法线/粗糙度/金属度/AO/Mask 属于线性数据。勾选 sRGB 会在采样时做一次 gamma 解码，"
                   "结果就是「光照看起来脏、法线方向发虚」，而且很多人查很久都找不到原因。",
            paths=srgb_on_linear[:40],
            hint="把 TextureImporter 的 sRGB 关掉（或用 Data/Linear 预设）。",
        ))
    if wrong_type:
        issues.append(Issue(
            "critical", "tex.normal_wrong_type",
            tr(f"{len(wrong_type)} 张疑似法线图没有设成 NormalMap 类型", f"{len(wrong_type)} normal maps are not imported as NormalMap"),
            detail="只有 textureType=NormalMap 时 Unity 才会做 DXT5nm 压缩与解包；"
                   "按 Default 导入会让法线方向整体错乱。",
            paths=wrong_type[:40],
        ))
    if readable:
        issues.append(Issue(
            "warn", "tex.readable",
            tr(f"{len(readable)} 张贴图勾了 Read/Write", f"{len(readable)} textures have Read/Write enabled"),
            detail="会在内存里保留一份 CPU 可读副本，贴图量一大内存直接翻倍。"
                   "只有需要 GetPixels / 运行时读写时才该打开。",
            paths=readable[:40],
        ))
    if oversized:
        issues.append(Issue(
            "warn", "tex.oversized",
            tr(f"{len(oversized)} 张非 UI 贴图超过 2048", f"{len(oversized)} non-UI textures exceed 2048px"),
            detail="移动端/低配机上 4096 贴图会显著拉高包体与显存峰值。",
            paths=oversized[:30],
        ))
    return issues


def check_audio(info: ProjectInfo, assets: list[Asset], index: MetaIndex) -> list[Issue]:
    issues: list[Issue] = []
    bgm_uncompressed: list[str] = []
    bgm_decompress_on_load: list[str] = []
    short_streaming: list[str] = []

    for asset in assets:
        if asset.category != "audio":
            continue
        meta = index.by_rel.get(asset.meta_rel or "")
        if meta is None:
            continue
        load_type = _meta_int(meta, "AudioImporter.defaultSettings.loadType",
                              "AudioImporter.loadType")
        compression = _meta_int(meta, "AudioImporter.defaultSettings.compressionFormat",
                                "AudioImporter.compressionFormat")
        info_wav = wav_info(asset.path) if asset.ext == ".wav" else None
        duration = info_wav["duration"] if info_wav else None

        if duration is not None and duration >= 10:
            if compression == 0:
                bgm_uncompressed.append(
                    f"{asset.rel} ({duration:.0f}s, PCM, {asset.size / 1024 / 1024:.1f} MB)"
                )
            if load_type == 0:
                bgm_decompress_on_load.append(f"{asset.rel} ({duration:.0f}s)")
        if duration is not None and duration < 1.5 and load_type == 2:
            short_streaming.append(f"{asset.rel} ({duration:.2f}s)")

    if bgm_uncompressed:
        issues.append(Issue(
            "warn", "audio.pcm_bgm",
            tr(f"{len(bgm_uncompressed)} 段长音频使用未压缩 PCM", f"{len(bgm_uncompressed)} long clips use uncompressed PCM"),
            detail="PCM 是 WAV 原始格式，等于把整段波形原样塞进包里；"
                   "背景音乐用 Vorbis 通常能压到 1/10 且听感差异很小。",
            paths=bgm_uncompressed[:30],
        ))
    if bgm_decompress_on_load:
        issues.append(Issue(
            "warn", "audio.decompress_bgm",
            tr(f"{len(bgm_decompress_on_load)} 段长音频是 DecompressOnLoad", f"{len(bgm_decompress_on_load)} long clips use DecompressOnLoad"),
            detail="整段解码进内存（PCM 尺寸），BGM 应该用 CompressedInMemory 或 Streaming。",
            paths=bgm_decompress_on_load[:30],
        ))
    if short_streaming:
        issues.append(Issue(
            "warn", "audio.short_streaming",
            tr(f"{len(short_streaming)} 个短音效用了 Streaming", f"{len(short_streaming)} short SFX use Streaming"),
            detail="Streaming 每次播放都要走磁盘 IO，短音效（脚步、UI 点击）会有明显延迟。",
            paths=short_streaming[:30],
        ))
    return issues


def check_models(info: ProjectInfo, assets: list[Asset], index: MetaIndex) -> list[Issue]:
    readable: list[str] = []
    embedded_materials: list[str] = []
    for asset in assets:
        if asset.category != "model" or asset.ext != ".fbx":
            continue
        meta = index.by_rel.get(asset.meta_rel or "")
        if meta is None:
            continue
        if _meta_int(meta, "ModelImporter.isReadable",
                     "ModelImporter.animations.isReadable",
                     "ModelImporter.meshes.isReadable") == 1:
            readable.append(asset.rel)
        if _meta_int(meta, "ModelImporter.importMaterials") == 1:
            embedded_materials.append(asset.rel)

    issues: list[Issue] = []
    if readable:
        issues.append(Issue(
            "warn", "model.readable",
            tr(f"{len(readable)} 个模型勾了 Read/Write", f"{len(readable)} models have Read/Write enabled"),
            detail="模型网格会在内存中保留一份 CPU 副本，网格多的项目很吃内存。",
            paths=readable[:30],
        ))
    if embedded_materials:
        issues.append(Issue(
            "info", "model.embedded_materials",
            tr(f"{len(embedded_materials)} 个 FBX 使用内嵌材质（importMaterials=1）", f"{len(embedded_materials)} FBX files embed their materials"),
            detail="内嵌材质会随 FBX 生成到 .fbm 目录，难以统一改参数、难以复用。",
            paths=embedded_materials[:30],
        ))
    return issues
