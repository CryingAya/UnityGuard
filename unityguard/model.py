"""Shared data types for the audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .i18n import tr

SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}
SEVERITY_LABEL = {"critical": "严重", "warn": "警告", "info": "提示"}
_SEVERITY_LABEL_EN = {"critical": "Critical", "warn": "Warning", "info": "Note"}


def severity_label(severity: str) -> str:
    return tr(SEVERITY_LABEL.get(severity, severity),
              _SEVERITY_LABEL_EN.get(severity, severity))


@dataclass
class Issue:
    """One finding. `paths` are project-relative POSIX paths."""

    severity: str
    code: str
    title: str
    detail: str = ""
    paths: list[str] = field(default_factory=list)
    hint: str = ""

    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER.get(self.severity, 9), self.code, self.title)


@dataclass
class Asset:
    """A file inside the Unity project (Assets/ only, .meta excluded)."""

    path: Path
    rel: str
    size: int
    mtime: float
    ext: str
    category: str
    guid: str | None = None
    has_meta: bool = False
    meta_rel: str | None = None

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_in_resources(self) -> bool:
        parts = self.rel.split("/")
        return "Resources" in parts[:-1]

    @property
    def is_in_editor(self) -> bool:
        return "Editor" in self.rel.split("/")[:-1]

    @property
    def is_in_streaming_assets(self) -> bool:
        return self.rel.startswith("Assets/StreamingAssets/")


@dataclass
class MetaFile:
    """A parsed .meta file."""

    path: Path
    rel: str
    guid: str | None
    importer: str | None
    props: dict[str, str] = field(default_factory=dict)
    companion_rel: str | None = None  # the asset / folder this meta belongs to

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.props.get(key, default)

    def get_int(self, key: str) -> int | None:
        raw = self.props.get(key)
        if raw is None:
            return None
        try:
            return int(raw.strip())
        except ValueError:
            return None

    def get_bool(self, key: str) -> bool | None:
        value = self.get_int(key)
        if value is None:
            return None
        return bool(value)
