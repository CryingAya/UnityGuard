#!/usr/bin/env python3
"""Local feasibility check for the execution engine.

Proves the three claims the paid tier rests on, on throw-away copies:

1. 改名/移动 + 同步 `.meta` 之后，GUID 不变，工程里的引用一条都不会断。
2. 回滚能把整棵树复原到**字节级**（路径 + 内容哈希完全一致）。
3. 中途"崩溃"之后，可以用日志把已做的部分撤干净；重复执行同一计划是幂等的。

    python tools/feasibility_check.py            # 合成工程 + 真实工程副本
    python tools/feasibility_check.py --keep     # 保留现场方便手动看
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unityguard.apply import apply_plan, rollback_journal  # noqa: E402
from unityguard.cli import run_audit  # noqa: E402
from unityguard.metaedit import build_fix_plan  # noqa: E402
from unityguard.metainfo import build_index, parse_meta  # noqa: E402
from unityguard.plan import build_plan  # noqa: E402
from unityguard.project import build_assets, locate  # noqa: E402

WORK = ROOT / ".tmp_feasibility"
CHECKED_CODES = ("meta.missing", "meta.duplicate_guid", "ref.dangling", "meta.orphan")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    """Path -> content hash for everything Unity would see (ignore our own state)."""
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if not path.is_file():
            continue
        if rel.startswith(".unityguard/") or rel.startswith("Temp/"):
            continue
        out[rel] = sha256(path)
    return out


def counts(result: dict) -> dict[str, int]:
    return {code: sum(1 for issue in result["issues"] if issue["code"] == code)
            for code in CHECKED_CODES}


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.checks += 1
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f"  — {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(label)


def make_sample(destination: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_sample_project.py"),
                    str(destination)], check=True, stdout=subprocess.DEVNULL)


def ensure_folder_metas(root: Path) -> int:
    """A package checkout has no folder `.meta`; an imported package does.

    Unity writes one for every folder on import, so the realistic state is
    "every folder has a meta" - recreate that before testing.
    """
    import uuid

    created = 0
    for path in sorted(root.rglob("*")):
        if not path.is_dir() or path.name == "Assets":
            continue
        if path.relative_to(root).parts[0] != "Assets":
            continue
        meta = Path(str(path) + ".meta")
        if not meta.exists():
            meta.write_text(
                "fileFormatVersion: 2\n"
                f"guid: {uuid.uuid4().hex}\n"
                "folderAsset: yes\n"
                "DefaultImporter:\n"
                "  externalObjects: {}\n"
                "  userData: \n",
                encoding="utf-8",
            )
            created += 1
    return created


def synthetic_case(report: Report) -> None:
    print("\n[1/6] 合成工程：改名 / 跨目录移动 / 仅改大小写")
    project = WORK / "synthetic"
    if project.exists():
        shutil.rmtree(project)
    make_sample(project)

    info = locate(project)
    before_audit = run_audit(project)
    before_tree = snapshot(project)

    pairs = [
        # 同目录改名
        ("Assets/Art/Textures/hero_basecolor.png",
         "Assets/Art/Textures/hero_basecolor_v2.png"),
        # 跨目录移动（新建文件夹）
        ("Assets/Art/Textures/Sword final copy (1).png",
         "Assets/Weapons/sword_01.png"),
        ("Assets/Audio/sfx/sfx_step.wav", "Assets/Audio/Footsteps/step_01.wav"),
        # 仅改大小写（Windows 特例）
        ("Assets/Art/Textures/BG_Forest 01.png",
         "Assets/Art/Textures/bg_forest_01.png"),
    ]
    plan = build_plan(info, pairs, rule="feasibility")
    report.check("计划无校验错误", not plan.errors, "; ".join(plan.errors))
    report.check("4 个操作全部进入计划", len(plan.operations) == 4)

    expected_guids = {op.to_rel: op.guid for op in plan.operations if op.guid}

    result = apply_plan(info, plan, tier="pro")
    report.check("执行成功", result.ok, "; ".join(result.failed))
    report.check("4 个操作全部执行", result.applied == 4, f"applied={result.applied}")

    # 1) GUID 与 .meta 成对
    for to_rel, guid in expected_guids.items():
        meta_path = project / (to_rel + ".meta")
        report.check(f".meta 跟随移动：{Path(to_rel).name}", meta_path.is_file())
        if meta_path.is_file():
            after_guid = parse_meta(meta_path, to_rel + ".meta").guid
            report.check(f"GUID 不变：{Path(to_rel).name}", after_guid == guid,
                         f"{guid} -> {after_guid}")

    # 2) 引用没有断：体检里的断链/缺 meta 数量不变
    after_audit = run_audit(project)
    before_counts, after_counts = counts(before_audit), counts(after_audit)
    report.check("体检结论不变（无新增断链 / 缺 meta）",
                 before_counts == after_counts,
                 f"{before_counts} -> {after_counts}")

    # 3) 幂等：同一份计划再执行一次
    again = apply_plan(info, plan, tier="pro")
    report.check("重复执行是幂等的（0 个新操作）", again.ok and again.applied == 0,
                 f"applied={again.applied}, failed={again.failed}")

    # 4) 回滚到字节级一致
    rollback = rollback_journal(Path(result.journal_path))
    report.check("回滚成功", rollback.ok, "; ".join(rollback.failed))
    restored = snapshot(project)
    report.check("整棵树字节级复原", restored == before_tree,
                 f"差异 {len(set(before_tree) ^ set(restored))} 个路径")
    journal_dir = project / ".unityguard"
    report.check("新建目录已清理",
                 not (project / "Assets" / "Weapons").exists()
                 and not (project / "Assets" / "Audio" / "Footsteps").exists())
    _ = journal_dir


def crash_case(report: Report) -> None:
    print("\n[2/6] 模拟中途崩溃：日志能否把半成品撤干净")
    project = WORK / "crash"
    if project.exists():
        shutil.rmtree(project)
    make_sample(project)

    info = locate(project)
    before_tree = snapshot(project)
    pairs = [
        ("Assets/Art/Textures/hero_basecolor.png", "Assets/Art/Textures/a.png"),
        ("Assets/Art/Textures/hero_normal.png", "Assets/Art/Textures/b.png"),
        ("Assets/Art/Textures/hero_roughness.png", "Assets/Art/Textures/c.png"),
        ("Assets/Art/Textures/huge_diffuse.png", "Assets/Art/Textures/d.png"),
    ]
    plan = build_plan(info, pairs)
    result = apply_plan(info, plan, tier="pro", fail_after=2)
    report.check("崩溃被报告出来", not result.ok, "")
    report.check("完成了 2 个操作", result.applied == 2, f"applied={result.applied}")

    rollback = rollback_journal(Path(result.journal_path))
    report.check("崩溃后回滚成功", rollback.ok, "; ".join(rollback.failed))
    report.check("崩溃后仍然字节级复原", snapshot(project) == before_tree)


def real_case(report: Report) -> None:
    print("\n[3/6] 真实工程（Unity 官方 2d-extras）：改一张被引用的贴图")
    source = ROOT / "fixtures" / "real" / "2dx"
    if not source.is_dir():
        print("  [SKIP] 没有 fixtures/real/2dx")
        return
    project = WORK / "real"
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(source, project)

    info = locate(project)
    before_audit = run_audit(project)
    before_tree = snapshot(project)
    before_counts = counts(before_audit)

    target = "Assets/Samples/DungeonRuleTile/Sprites/Dungeon.png"
    if not (project / target).is_file():
        print(f"  [SKIP] 找不到 {target}")
        return
    old_guid = parse_meta(project / (target + ".meta"), target + ".meta").guid
    new_rel = "Assets/Samples/DungeonRuleTile/Sprites/Dungeon_Tileset.png"

    plan = build_plan(info, [(target, new_rel)], rule="feasibility-real")
    report.check("真实工程计划有效", not plan.errors, "; ".join(plan.errors))
    result = apply_plan(info, plan, tier="pro")
    report.check("真实工程执行成功", result.ok, "; ".join(result.failed))

    new_guid = parse_meta(project / (new_rel + ".meta"), new_rel + ".meta").guid
    report.check("真实 GUID 未变", new_guid == old_guid, f"{old_guid} -> {new_guid}")
    referencing = project / "Assets/Samples/DungeonRuleTile/Tiles/Dungeon.asset"
    if referencing.is_file():
        text = referencing.read_text(encoding="utf-8", errors="replace")
        report.check("引用方仍然指向同一个 GUID", old_guid in text)

    after_audit = run_audit(project)
    report.check("真实工程体检结论不变（0 新增断链）",
                 counts(after_audit)["ref.dangling"] == before_counts["ref.dangling"],
                 f"{before_counts} -> {counts(after_audit)}")

    rollback = rollback_journal(Path(result.journal_path))
    report.check("真实工程回滚成功", rollback.ok, "; ".join(rollback.failed))
    report.check("真实工程字节级复原", snapshot(project) == before_tree)


def folder_case(report: Report) -> None:
    print("\n[4/6] 真实工程：整体搬一个文件夹（含目录 .meta 与内部所有资源）")
    source = ROOT / "fixtures" / "real" / "2dx"
    if not source.is_dir():
        print("  [SKIP] 没有 fixtures/real/2dx")
        return
    project = WORK / "folder"
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(source, project)
    added = ensure_folder_metas(project)
    print(f"  （补齐 {added} 个目录 .meta，模拟已导入工程的真实状态）")

    info = locate(project)
    before_tree = snapshot(project)
    before_counts = counts(run_audit(project))

    folder = "Assets/Samples/DungeonRuleTile"
    if not (project / folder).is_dir():
        print(f"  [SKIP] 找不到 {folder}")
        return
    folder_meta = project / (folder + ".meta")
    report.check("源目录有 .meta（已导入工程的常态）", folder_meta.is_file())
    folder_guid = parse_meta(folder_meta, "m").guid if folder_meta.is_file() else None
    inner_guid = parse_meta(project / f"{folder}/Sprites/Dungeon.png.meta", "m").guid
    target = "Assets/Packs/DungeonRuleTile"

    plan = build_plan(info, [(folder, target)], rule="feasibility-folder")
    report.check("文件夹计划有效", not plan.errors, "; ".join(plan.errors))
    report.check("计划识别为目录操作",
                 bool(plan.operations) and plan.operations[0].is_dir)

    result = apply_plan(info, plan, tier="pro")
    report.check("文件夹移动执行成功", result.ok, "; ".join(result.failed))
    report.check("原目录已消失", not (project / folder).exists())
    report.check("目录 .meta 跟随移动", (project / (target + ".meta")).is_file())
    report.check("目录 GUID 未变",
                 folder_guid is not None
                 and parse_meta(project / (target + ".meta"), "m").guid == folder_guid)
    report.check("目录内资源完整",
                 (project / f"{target}/Tiles/Dungeon.asset").is_file()
                 and (project / f"{target}/Sprites/Dungeon.png").is_file())
    report.check("内部资源 GUID 未变",
                 parse_meta(project / f"{target}/Sprites/Dungeon.png.meta", "m").guid
                 == inner_guid)

    after_counts = counts(run_audit(project))
    report.check("搬完目录后仍然 0 新增断链",
                 after_counts["ref.dangling"] == before_counts["ref.dangling"],
                 f"{before_counts} -> {after_counts}")

    rollback = rollback_journal(Path(result.journal_path))
    report.check("文件夹回滚成功", rollback.ok, "; ".join(rollback.failed))
    report.check("文件夹回滚字节级复原", snapshot(project) == before_tree)


def fix_case(report: Report) -> None:
    print("\n[5/6] 一键修复导入设置：体检 → 修复 → 问题消失 → 回滚复原")
    project = WORK / "fix"
    if project.exists():
        shutil.rmtree(project)
    make_sample(project)

    info = locate(project)
    before_audit = run_audit(project)
    before_codes = {issue["code"] for issue in before_audit["issues"]}
    before_tree = snapshot(project)
    before_guids = {path.relative_to(project).as_posix(): parse_meta(path, "m").guid
                    for path in project.rglob("*.meta")
                    if ".unityguard" not in path.parts}

    assets = build_assets(info)
    index = build_index(info, assets)
    plan = build_fix_plan(info, assets, index)
    report.check("从体检结果生成修复计划", len(plan.operations) >= 5,
                 f"{len(plan.operations)} 个操作")

    result = apply_plan(info, plan, tier="pro")
    report.check("修复执行成功", result.ok, "; ".join(result.failed))

    after_audit = run_audit(project)
    after_codes = {issue["code"] for issue in after_audit["issues"]}
    for code in ("tex.srgb_on_linear", "tex.normal_wrong_type", "audio.pcm_bgm",
                 "audio.decompress_bgm", "audio.short_streaming", "model.readable"):
        report.check(f"{code} 已消失", code in before_codes and code not in after_codes)

    after_guids = {path.relative_to(project).as_posix(): parse_meta(path, "m").guid
                   for path in project.rglob("*.meta")
                   if ".unityguard" not in path.parts}
    report.check("GUID 全部未变（只改了导入设置）", after_guids == before_guids)

    score_before = before_audit["summary"]["score"]
    score_after = after_audit["summary"]["score"]
    report.check(f"健康分上升 {score_before} → {score_after}", score_after > score_before)

    rollback = rollback_journal(Path(result.journal_path))
    report.check("修复可以回滚", rollback.ok, "; ".join(rollback.failed))
    report.check("回滚后字节级复原", snapshot(project) == before_tree)


def swap_case(report: Report) -> None:
    print("\n[6/6] 名称互换：自动拆临时名 → 落回目标名，GUID 跟着文件走")
    source = ROOT / "fixtures" / "real" / "2dx"
    if not source.is_dir():
        print("  [SKIP] 没有 fixtures/real/2dx")
        return
    project = WORK / "swap"
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(source, project)
    ensure_folder_metas(project)

    info = locate(project)
    before_tree = snapshot(project)
    before_counts = counts(run_audit(project))

    a = "Assets/Samples/DungeonRuleTile/Sprites/Dungeon.png"
    b = "Assets/Samples/PipeRuleTile/Sprites/Pipes.png"
    if not (project / a).is_file() or not (project / b).is_file():
        print("  [SKIP] 找不到可互换的两个贴图")
        return
    a_bytes = (project / a).read_bytes()
    b_bytes = (project / b).read_bytes()
    a_guid = parse_meta(project / (a + ".meta"), "m").guid
    b_guid = parse_meta(project / (b + ".meta"), "m").guid

    plan = build_plan(info, [(a, b), (b, a)], rule="feasibility-swap")
    report.check("互换计划有效", not plan.errors, "; ".join(plan.errors))
    report.check("自动插入 1 个临时步骤",
                 sum(1 for op in plan.operations if op.is_temp) == 1)

    result = apply_plan(info, plan, tier="pro")
    report.check("互换执行成功", result.ok, "; ".join(result.failed))
    report.check("内容已互换", (project / a).read_bytes() == b_bytes
                 and (project / b).read_bytes() == a_bytes)
    report.check("GUID 跟着文件走（a 的 guid 在 b 上）",
                 parse_meta(project / (b + ".meta"), "m").guid == a_guid)
    report.check("没有残留临时文件", not list(project.rglob("*ugswap*")))

    after_counts = counts(run_audit(project))
    report.check("互换后仍然 0 新增断链",
                 after_counts["ref.dangling"] == before_counts["ref.dangling"],
                 f"{before_counts} -> {after_counts}")

    rollback = rollback_journal(Path(result.journal_path))
    report.check("互换可回滚", rollback.ok, "; ".join(rollback.failed))
    report.check("互换回滚后字节级复原", snapshot(project) == before_tree)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留工作目录")
    args = parser.parse_args()

    WORK.mkdir(exist_ok=True)
    report = Report()
    try:
        synthetic_case(report)
        crash_case(report)
        real_case(report)
        folder_case(report)
        fix_case(report)
        swap_case(report)
    finally:
        if not args.keep:
            shutil.rmtree(WORK, ignore_errors=True)

    print()
    if report.failures:
        print(f"结论：{len(report.failures)} / {report.checks} 项未通过")
        for item in report.failures:
            print(f"  - {item}")
        return 1
    print(f"结论：{report.checks} / {report.checks} 项通过，执行引擎的可行性已验证")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
