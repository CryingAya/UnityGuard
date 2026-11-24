"""Tests for content de-duplication (`plan --rule dedupe`)."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("UNITYGUARD_LANG", "zh")

from unityguard.apply import apply_plan, rollback_journal  # noqa: E402
from unityguard.cli import run_audit  # noqa: E402
from unityguard.dedupe import QUARANTINE_DIR, build_dedupe_plan, find_groups  # noqa: E402
from unityguard.plan import load_plan, save_plan  # noqa: E402
from unityguard.scan import scan  # noqa: E402


def _guid(seed: str) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def write_meta(path: Path, seed: str) -> None:
    path.write_text(
        "fileFormatVersion: 2\n"
        f"guid: {_guid(seed)}\n"
        "TextureImporter:\n"
        "  sRGBTexture: 1\n"
        "  textureType: 0\n",
        encoding="utf-8",
    )


def tiny_project(base: Path) -> Path:
    root = base / "p"
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.1f1\n", encoding="utf-8")
    art = root / "Assets" / "Art"
    art.mkdir(parents=True)

    png = b"\x89PNG\r\n\x1a\n" + b"identical-bytes" * 8
    # 两份完全相同的贴图；hero 被预制体引用，hero_copy 没有
    (art / "hero.png").write_bytes(png)
    write_meta(art / "hero.png.meta", "hero")
    (art / "hero_copy.png").write_bytes(png)
    write_meta(art / "hero_copy.png.meta", "hero_copy")

    # 两份都没被引用
    (art / "unused.png").write_bytes(b"orphan-bytes" * 16)
    write_meta(art / "unused.png.meta", "unused")
    (art / "unused_dup.png").write_bytes(b"orphan-bytes" * 16)
    write_meta(art / "unused_dup.png.meta", "unused_dup")

    # Resources 里的重复不能被隔离（字符串路径加载）
    res = root / "Assets" / "Resources"
    res.mkdir()
    (res / "logo.png").write_bytes(b"resource-bytes" * 8)
    write_meta(res / "logo.png.meta", "logo")
    (res / "logo2.png").write_bytes(b"resource-bytes" * 8)
    write_meta(res / "logo2.png.meta", "logo2")

    # 预制体引用 hero
    prefabs = root / "Assets" / "Prefabs"
    prefabs.mkdir()
    (prefabs / "hero.prefab").write_text(
        "%YAML 1.1\n--- !u!114 &1\nMonoBehaviour:\n"
        f"  m_Texture: {{fileID: 2800000, guid: {_guid('hero')}, type: 3}}\n",
        encoding="utf-8")
    write_meta(prefabs / "hero.prefab.meta", "hero.prefab")

    # 让预制体可达
    (root / "ProjectSettings" / "EditorBuildSettings.asset").write_text(
        "%YAML 1.1\n--- !u!1045 &1\nEditorBuildSettings:\n  m_Scenes: []\n"
        f"  m_configObjects: {{}}\n  m_Extra: {{fileID: 1, guid: {_guid('hero.prefab')}, type: 3}}\n",
        encoding="utf-8")
    return root


class DedupeCase(unittest.TestCase):
    def setUp(self) -> None:
        base = ROOT / ".tmp_tests"
        base.mkdir(exist_ok=True)
        self.work = base / uuid.uuid4().hex[:10]
        self.work.mkdir()
        self.root = tiny_project(self.work)
        self.state = scan(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)


class TestGrouping(DedupeCase):
    def test_keeps_the_referenced_copy(self):
        groups = find_groups(self.state)
        hero_group = next(g for g in groups
                          if any(a.rel.endswith("hero.png") for a in
                                 ([g.keep] if g.keep else [])))
        self.assertTrue(hero_group.keep.rel.endswith("hero.png"))
        self.assertEqual([a.rel for a in hero_group.hide],
                         ["Assets/Art/hero_copy.png"])

    def test_resources_are_ignored(self):
        rels = {a.rel for g in find_groups(self.state) for a in ([g.keep] if g.keep else []) + g.hide}
        self.assertNotIn("Assets/Resources/logo.png", rels)
        self.assertNotIn("Assets/Resources/logo2.png", rels)

    def test_unreferenced_group_keeps_one(self):
        group = next(g for g in find_groups(self.state)
                     if any(a.rel.endswith("unused.png") for a in
                            ([g.keep] if g.keep else []) + g.hide))
        self.assertEqual(len(group.hide), 1)
        self.assertTrue(group.keep.rel.endswith("unused.png"))


class TestPlanAndApply(DedupeCase):
    def test_plan_targets_quarantine_folder(self):
        plan = build_dedupe_plan(self.state)
        self.assertFalse(plan.errors, plan.errors)
        targets = [op.to_rel for op in plan.operations]
        self.assertTrue(all(t.startswith(QUARANTINE_DIR + "/") for t in targets))
        self.assertIn("Assets/Art/hero_copy.png",
                      [op.from_rel for op in plan.operations])
        self.assertNotIn("Assets/Resources/logo2.png",
                         [op.from_rel for op in plan.operations])

    def test_plan_roundtrip(self):
        plan = build_dedupe_plan(self.state)
        path = save_plan(plan, self.work / "plan.json")
        self.assertEqual(plan.digest(), load_plan(path).digest())

    def test_apply_quarantines_duplicates_with_meta(self):
        plan = build_dedupe_plan(self.state)
        result = apply_plan(self.state.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        moved = self.root / QUARANTINE_DIR / "Art" / "hero_copy.png"
        self.assertTrue(moved.is_file())
        self.assertTrue((moved.parent / "hero_copy.png.meta").is_file())
        self.assertFalse((self.root / "Assets/Art/hero_copy.png").exists())
        # Unity 忽略 ~ 目录，所以体检里只剩 5 个资源（隔离的那份不算）
        after = run_audit(self.root)
        self.assertEqual(after["summary"]["assets"], 5)

    def test_rollback_restores_everything(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in (self.root / "Assets").rglob("*") if path.is_file()}
        plan = build_dedupe_plan(self.state)
        result = apply_plan(self.state.info, plan, tier="pro")
        rollback = rollback_journal(Path(result.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in (self.root / "Assets").rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_no_duplicates_means_empty_plan(self):
        empty = self.work / "clean"
        shutil.copytree(self.root, empty)
        for name in ("Art/hero_copy.png", "Art/hero_copy.png.meta",
                     "Art/unused.png", "Art/unused.png.meta",
                     "Resources/logo2.png", "Resources/logo2.png.meta"):
            (empty / "Assets" / name).unlink()
        plan = build_dedupe_plan(scan(empty))
        self.assertEqual(plan.operations, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
