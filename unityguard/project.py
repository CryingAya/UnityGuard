"""Locate a Unity project and inventory its assets (read-only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import tr
from .model import Asset

# Directories that must never be audited (generated / huge / VCS internals).
SKIP_DIRS = {
    "Library",
    "Temp",
    "Obj",
    "obj",
    "Logs",
    "Build",
    "Builds",
    "UserSettings",
    "MemoryCaptures",
    "Recordings",
    ".git",
    ".svn",
    ".vs",
    ".idea",
    ".vscode",
    "node_modules",
}

CATEGORY_BY_EXT = {
    # audio
    ".wav": "audio", ".mp3": "audio", ".ogg": "audio", ".flac": "audio",
    ".aiff": "audio", ".aif": "audio", ".m4a": "audio", ".opus": "audio",
    # textures / 2D
    ".png": "texture", ".jpg": "texture", ".jpeg": "texture", ".tga": "texture",
    ".psd": "texture", ".exr": "texture", ".hdr": "texture", ".tif": "texture",
    ".tiff": "texture", ".bmp": "texture", ".dds": "texture",
    # models
    ".fbx": "model", ".obj": "model", ".blend": "model", ".glb": "model",
    ".gltf": "model", ".dae": "model", ".3ds": "model", ".ma": "model",
    ".mb": "model", ".max": "model",
    # shaders
    ".shader": "shader", ".hlsl": "shader", ".cginc": "shader",
    ".compute": "shader", ".shadergraph": "shader", ".shadersubgraph": "shader",
    ".hlslinclude": "shader",
    # scripts
    ".cs": "script", ".js": "script", ".dll": "script", ".asmdef": "script",
    ".asmref": "script",
    # fonts / video
    ".ttf": "font", ".otf": "font", ".fontsettings": "font",
    ".mp4": "video", ".mov": "video", ".webm": "video", ".avi": "video",
    # engine / data
    ".prefab": "engine", ".unity": "engine", ".mat": "engine", ".anim": "engine",
    ".controller": "engine", ".overridecontroller": "engine",
    ".playable": "engine", ".mixer": "engine", ".physicmaterial": "engine",
    ".rendertexture": "engine", ".spriteatlas": "engine",
    ".terrainlayer": "engine", ".guiskin": "engine", ".lighting": "engine",
    ".signal": "engine", ".brush": "engine", ".mask": "engine",
    ".asset": "engine", ".preset": "engine",
    ".json": "data", ".csv": "data", ".txt": "data", ".xml": "data",
    ".bytes": "data", ".yaml": "data", ".yml": "data", ".md": "data",
}

# Assets whose content is YAML and may reference other assets by GUID.
YAML_REF_EXTS = {
    ".prefab", ".unity", ".asset", ".mat", ".anim", ".controller",
    ".overridecontroller", ".playable", ".mixer", ".physicmaterial",
    ".rendertexture", ".spriteatlas", ".terrainlayer", ".guiskin",
    ".lighting", ".signal", ".mask", ".preset", ".brush",
}

MAX_REF_SCAN_BYTES = 64 * 1024 * 1024


def ignored_by_unity(name: str) -> bool:
    """Unity skips dotfiles and anything ending in `~` (Samples~, Docs~, .DS_Store).

    These never get a .meta, so they must be invisible to the audit too -
    otherwise every Unity package sample folder looks broken.
    """
    return name.startswith(".") or name.endswith("~")


@dataclass
class ProjectInfo:
    root: Path
    is_unity: bool
    assets_dir: Path
    unity_version: str | None = None
    unity_locked: bool = False
    has_meta_ignore: bool = False
    has_addressables: bool = False
    has_git: bool = False
    notes: list[str] = field(default_factory=list)

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def find_unity_root(start: Path) -> Path | None:
    """Walk up from `start` looking for Assets/ + ProjectSettings/."""
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / "Assets").is_dir() and (candidate / "ProjectSettings").is_dir():
            return candidate
    return None


def locate(start: Path) -> ProjectInfo:
    start = start.expanduser().resolve()
    root = find_unity_root(start) if start.is_dir() else find_unity_root(start.parent)
    if root is None:
        # Fall back: treat the given folder as the assets root.
        assets = start if start.name == "Assets" else start
        return ProjectInfo(
            root=assets,
            is_unity=False,
            assets_dir=assets,
            notes=["未找到 ProjectSettings/ProjectVersion.txt：按普通素材目录处理，"
                   "跳过 GUID / 引用相关检查。"],
        )

    assets_dir = root / "Assets"
    info = ProjectInfo(root=root, is_unity=True, assets_dir=assets_dir)

    version_file = root / "ProjectSettings" / "ProjectVersion.txt"
    if version_file.is_file():
        for line in _read_text(version_file).splitlines():
            if line.startswith("m_EditorVersion:"):
                info.unity_version = line.split(":", 1)[1].strip()
                break

    info.unity_locked = (root / "Temp" / "UnityLockfile").exists()
    info.has_git = (root / ".git").exists()
    info.has_addressables = (assets_dir / "AddressableAssetsData").is_dir()

    gitignore = root / ".gitignore"
    if gitignore.is_file():
        for raw in _read_text(gitignore).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line in ("*.meta", "*.meta/") or line.rstrip("/") == "*.meta":
                info.has_meta_ignore = True
                break

    if info.unity_locked:
        info.notes.append(
            "检测到 Temp/UnityLockfile：Unity 编辑器当前正在打开这个工程。"
            "只读体检可以继续，但任何改名/移动操作都必须先关闭编辑器。"
        )
    return info


def _read_text(path: Path, limit: int = 2 * 1024 * 1024) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def walk_assets(info: ProjectInfo) -> list[Path]:
    """All non-.meta files under Assets/, skipping generated folders."""
    found: list[Path] = []
    if not info.assets_dir.is_dir():
        return found
    for current, dirnames, filenames in os.walk(info.assets_dir):
        dirnames[:] = [
            name for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".git")
            and not ignored_by_unity(name)
        ]
        for name in filenames:
            if name.endswith(".meta") or ignored_by_unity(name):
                continue
            found.append(Path(current) / name)
    found.sort(key=lambda p: p.as_posix().lower())
    return found


def walk_metas(info: ProjectInfo) -> list[Path]:
    found: list[Path] = []
    if not info.assets_dir.is_dir():
        return found
    for current, dirnames, filenames in os.walk(info.assets_dir):
        dirnames[:] = [
            name for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".git")
            and not ignored_by_unity(name)
        ]
        for name in filenames:
            if name.endswith(".meta") and not ignored_by_unity(name):
                found.append(Path(current) / name)
    found.sort(key=lambda p: p.as_posix().lower())
    return found


def build_assets(info: ProjectInfo) -> list[Asset]:
    assets: list[Asset] = []
    for path in walk_assets(info):
        try:
            stat = path.stat()
        except OSError:
            continue
        ext = path.suffix.lower()
        assets.append(
            Asset(
                path=path,
                rel=info.rel(path),
                size=stat.st_size,
                mtime=stat.st_mtime,
                ext=ext,
                category=CATEGORY_BY_EXT.get(ext, "other"),
            )
        )
    return assets
