"""Cross-cutting checks that need the whole project at once."""

from __future__ import annotations

import re

from .i18n import tr
from .model import Asset, Issue
from .metainfo import MetaIndex
from .project import ProjectInfo
from .refs import RefGraph, code_string_hits

_SHADER_INCLUDE_EXTS = {".cginc", ".hlsl", ".hlslinclude", ".shaderinclude"}
_AUTO_LOADED_EXTS = {".asmdef", ".asmref", ".dll"}
_SHADER_DECL = re.compile(r'\bShader\s+"([^"]+)"')


def _shader_code_hits(graph: RefGraph, asset: Asset) -> list[str]:
    """`Shader.Find("Custom/RimGlow")` refers to the *declared* name, which is
    often different from the file name. Match both."""
    try:
        with asset.path.open("rb") as handle:
            head = handle.read(8192).decode("utf-8", errors="replace")
    except OSError:
        return []
    hits: set[str] = set()
    for name in _SHADER_DECL.findall(head):
        lowered = name.strip().lower()
        hits |= graph.code_strings.get(lowered, set())
        hits |= graph.code_strings.get(lowered.rsplit("/", 1)[-1], set())
    return sorted(hits)


def check_project(info: ProjectInfo) -> list[Issue]:
    issues: list[Issue] = []
    if info.unity_locked:
        issues.append(Issue(
            "warn", "project.unity_open",
            tr("Unity 编辑器正在打开这个工程", "Unity editor is currently open on this project"),
            detail="只读体检不受影响。但任何改名/移动都必须在编辑器关闭后执行，"
                   "否则 Unity 会看到「旧文件消失 + 新文件出现」，把它当成两个不同的资源。",
            hint="关闭 Unity 后重新体检，再执行变更。",
        ))
    if info.has_meta_ignore:
        issues.append(Issue(
            "critical", "project.meta_ignored",
            tr(".gitignore 忽略了 *.meta", ".gitignore ignores *.meta"),
            detail="这是 Unity 项目最经典的灾难：.meta 存着 GUID，丢了 .meta 就等于"
                   "资源换了身份证，所有场景/预制体的引用全部变成 Missing。",
            hint="立刻从 .gitignore 移除 *.meta，并把现有 .meta 全部提交进版本库。",
        ))
    if info.has_addressables:
        issues.append(Issue(
            "info", "project.addressables",
            tr("工程使用 Addressables", "The project uses Addressables"),
            detail="Addressables 的「地址键（address）」是字符串，改名不会自动同步地址键，"
                   "代码里按地址加载的地方会静默失败。建议改名后统一检查 Addressable Groups。",
        ))
    if info.unity_version:
        issues.append(Issue(
            "info", "project.version",
            tr(f"Unity 版本：{info.unity_version}", f"Unity version: {info.unity_version}"),
            detail="跨大版本升级（如 2021 → 6）后，导入设置与序列化格式可能被重写，"
                   "建议升级前先做一次基线快照。",
        ))
    return issues


def check_meta(index: MetaIndex, assets: list[Asset]) -> list[Issue]:
    issues: list[Issue] = []

    if index.duplicate_guids:
        groups = sorted(index.duplicate_guids.items(), key=lambda item: -len(item[1]))
        paths: list[str] = []
        for guid, metas in groups[:20]:
            paths.append(f"GUID {guid} → " + " | ".join(meta.rel for meta in metas))
        issues.append(Issue(
            "critical", "meta.duplicate_guid",
            tr(f"{len(index.duplicate_guids)} 个 GUID 被多个 .meta 共用", f"{len(index.duplicate_guids)} GUIDs are shared by multiple .meta files"),
            detail="Unity 遇到重复 GUID 时取哪一个是不确定的（通常取先导入的那个），"
                   "引用的对象会随机变成另一份资源。多半是复制粘贴文件夹时连 .meta 一起复制造成的。",
            paths=paths,
            hint="删除多余的 .meta，让 Unity 重新生成 GUID，然后再重新指向引用。",
        ))

    if index.assets_missing_meta:
        rels = sorted(asset.rel for asset in index.assets_missing_meta)
        issues.append(Issue(
            "critical", "meta.missing",
            tr(f"{len(rels)} 个资源缺少 .meta", f"{len(rels)} assets have no .meta"),
            detail="缺 .meta 的资源会被 Unity 当成「新资源」重新分配 GUID，"
                   "原来指向它的场景/预制体引用会全部断开。",
            paths=rels[:40],
            hint="如果这些资源本来就在工程里，先确认它们是否曾被版本库忽略；"
                 "如果是新拷进来的，导入前先补齐 .meta。",
        ))

    if index.orphan_metas:
        rels = sorted(meta.rel for meta in index.orphan_metas)
        issues.append(Issue(
            "warn", "meta.orphan",
            tr(f"{len(rels)} 个 .meta 找不到对应文件", f"{len(rels)} .meta files have no matching asset"),
            detail="对应资源已被删除但 .meta 留在原地。数量多了会污染版本库，"
                   "也可能在你重建同名文件时「复活」一个旧 GUID。",
            paths=rels[:40],
        ))

    if index.metas_without_guid:
        rels = sorted(meta.rel for meta in index.metas_without_guid)
        issues.append(Issue(
            "critical", "meta.no_guid",
            tr(f"{len(rels)} 个 .meta 没有合法 guid", f"{len(rels)} .meta files have no valid guid"),
            detail="Unity 会重写这些 .meta（分配新 GUID），等于引用全部失效。",
            paths=rels[:40],
        ))

    if index.folder_metas:
        issues.append(Issue(
            "info", "meta.folder_meta",
            tr(f"检测到 {index.folder_metas} 个目录级 .meta", f"{index.folder_metas} folder .meta files found"),
            detail="目录也有 .meta（存目录 GUID）。重命名文件夹时，`Folder.meta` 必须跟着一起改，"
                   "否则目录引用会断。",
        ))
    return issues


def check_serialization(graph: RefGraph) -> list[Issue]:
    if graph.reference_analysis_usable:
        return []
    sample = sorted(graph.binary_assets)
    return [Issue(
        "warn", "project.binary_serialization",
        tr(f"{len(sample)} 个场景/预制体是二进制序列化，引用分析已停用", f"{len(sample)} scenes/prefabs are binary-serialized; reference analysis disabled"),
        detail="Unity 5.x 默认用二进制保存场景与预制体（.unity/.prefab 里是序列化数据，不是 YAML），"
               "外部工具无法可靠解析其中的 GUID 引用。这个工程里「哪些资源没被用到」的判断会全部失真，"
               "所以本报告不出「未使用」结论，只做 .meta / 命名 / 导入设置 / 重复文件检查。",
        paths=sample[:20],
        hint="把 Edit > Project Settings > Editor > Asset Serialization 改成 Force Text，"
             "重新保存场景后再体检，引用分析才可用。",
    )]


def check_references(graph: RefGraph, assets: list[Asset]) -> list[Issue]:
    issues: list[Issue] = []
    if graph.missing_targets:
        lines: list[str] = []
        for source, guids in sorted(graph.missing_targets.items())[:20]:
            for guid in sorted(guids):
                lines.append(f"{source} → guid:{guid}")
        total = sum(len(guids) for guids in graph.missing_targets.values())
        issues.append(Issue(
            "critical", "ref.dangling",
            tr(f"{total} 处引用指向不存在的 GUID", f"{total} references point at a missing GUID"),
            detail="这些就是 Inspector 里红色的 Missing (Object)。常见原因：删资源没删引用、"
                   ".meta 丢失导致 GUID 变化、资源包合并时覆盖。",
            paths=lines,
        ))
    return issues


def check_unused(graph: RefGraph, assets: list[Asset]) -> list[Issue]:
    if not graph.reference_analysis_usable:
        # Everything would look unreferenced: refuse to guess.
        return []

    never: list[str] = []
    unreachable: list[str] = []
    code_loaded: list[str] = []
    dead_scripts: list[str] = []

    for asset in assets:
        if not asset.guid:
            continue
        if asset.is_in_resources or asset.is_in_streaming_assets:
            continue
        if asset.is_in_editor:
            continue
        if asset.ext in _SHADER_INCLUDE_EXTS or asset.ext in _AUTO_LOADED_EXTS:
            continue
        if asset.rel.startswith("Assets/Plugins/") and asset.ext == ".dll":
            continue
        if asset.guid in graph.reachable:
            continue

        hits = code_string_hits(graph, asset)
        if not hits and asset.ext == ".shader":
            hits = _shader_code_hits(graph, asset)
        if hits:
            code_loaded.append(f"{asset.rel} ← {', '.join(hits[:2])}")
            continue
        if asset.category == "script":
            dead_scripts.append(asset.rel)
            continue
        referenced_by = graph.referenced_by(asset.guid)
        if referenced_by:
            unreachable.append(asset.rel)
        else:
            never.append(asset.rel)

    issues: list[Issue] = []
    if never:
        issues.append(Issue(
            "warn", "ref.never_referenced",
            tr(f"{len(never)} 个资源从未被任何文件引用", f"{len(never)} assets are never referenced"),
            detail="从构建场景 / Resources / ProjectSettings 出发都走不到，也没有任何资源指向它。"
                   "大概率是废弃素材，删掉可以直接减小包体和导入时间。",
            paths=sorted(never)[:60],
            hint="先确认没有通过代码字符串或 Addressables 远程加载，再移入回收目录。",
        ))
    if unreachable:
        issues.append(Issue(
            "warn", "ref.unreachable",
            tr(f"{len(unreachable)} 个资源只被「同样用不到」的资源引用", f"{len(unreachable)} assets are only referenced by other unused assets"),
            detail="它们之间可能互相引用，形成一个孤岛（例如一套已经废弃的材质+贴图）。"
                   "单看引用计数会以为它们在用，所以必须做可达性分析才能发现。",
            paths=sorted(unreachable)[:60],
        ))
    if dead_scripts:
        issues.append(Issue(
            "info", "ref.dead_scripts",
            tr(f"{len(dead_scripts)} 个脚本没有被任何场景/预制体挂载", f"{len(dead_scripts)} scripts are not attached to anything"),
            detail="不等于死代码：[InitializeOnLoad]、[MenuItem]、[CustomEditor] 这类特性"
                   "会在编辑器启动时自动加载，不需要被引用。Editor 目录下的脚本已自动排除。",
            paths=sorted(dead_scripts)[:60],
        ))
    if code_loaded:
        issues.append(Issue(
            "info", "ref.code_string",
            tr(f"{len(code_loaded)} 个资源只通过代码字符串加载", f"{len(code_loaded)} assets are loaded by code strings only"),
            detail="例如 Resources.Load / AssetDatabase.LoadAssetAtPath / Addressables 地址。"
                   "改名后这些加载会静默失败（返回 null 而不报错），必须人工核对。",
            paths=sorted(code_loaded)[:60],
        ))
    return issues
