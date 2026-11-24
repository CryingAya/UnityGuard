"""One read-only scan of a project, shared by audit and the planners."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .metainfo import MetaIndex, build_index
from .model import Asset
from .project import ProjectInfo, build_assets, locate
from .refs import RefGraph, build_graph


@dataclass
class Scan:
    info: ProjectInfo
    assets: list[Asset]
    index: MetaIndex
    graph: RefGraph

    @property
    def root(self) -> Path:
        return self.info.root


def scan(target: Path | str) -> Scan:
    info = locate(Path(target))
    assets = build_assets(info)
    index = build_index(info, assets)
    graph = build_graph(info, assets, index)
    return Scan(info=info, assets=assets, index=index, graph=graph)
