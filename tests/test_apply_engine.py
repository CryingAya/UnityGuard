"""Execution engine tests: plan validation, apply safety, rollback, licensing.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("UNITYGUARD_LANG", "zh")

from unityguard.apply import apply_plan, rollback_journal  # noqa: E402
from unityguard.licensing import (FREE_MAX_OPS, License, check_apply,  # noqa: E402
                                  issue_key, load_license, validate_key)
from unityguard.metainfo import parse_meta  # noqa: E402
from unityguard.plan import build_plan, load_plan, save_plan  # noqa: E402
from unityguard.project import locate  # noqa: E402


def _guid(seed: str) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def make_meta(path: Path, seed: str) -> None:
    path.write_text(
        "fileFormatVersion: 2\n"
        f"guid: {_guid(seed)}\n"
        "TextureImporter:\n"
        "  serializedVersion: 12\n"
        "  mipmaps:\n"
        "    sRGBTexture: 1\n"
        "  textureType: 0\n",
        encoding="utf-8",
    )


def make_folder_meta(path: Path, seed: str) -> None:
    path.write_text(
        "fileFormatVersion: 2\n"
        f"guid: {_guid(seed)}\n"
        "folderAsset: yes\n"
        "DefaultImporter:\n"
        "  externalObjects: {}\n"
        "  userData: \n",
        encoding="utf-8",
    )


def tiny_project(base: Path) -> Path:
    root = base / "p"
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.1f1\n", encoding="utf-8")
    tex = root / "Assets" / "Tex"
    tex.mkdir(parents=True)
    make_folder_meta(root / "Assets" / "Tex.meta", "folder:Tex")
    for name in ("a.png", "b.png", "c.png"):
        (tex / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
        make_meta(tex / (name + ".meta"), name)
    (root / "Assets" / "Audio").mkdir()
    make_folder_meta(root / "Assets" / "Audio.meta", "folder:Audio")
    (root / "Assets" / "Audio" / "s.wav").write_bytes(b"RIFF....WAVEfmt ")
    make_meta(root / "Assets" / "Audio" / "s.wav.meta", "s.wav")
    return root


def tree(root: Path) -> dict[str, str]:
    out = {}
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".unityguard/"):
                continue
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        base = ROOT / ".tmp_tests"
        base.mkdir(exist_ok=True)
        self.work = base / uuid.uuid4().hex[:10]
        self.work.mkdir()
        self.root = tiny_project(self.work)
        self.info = locate(self.root)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.work, ignore_errors=True)


class TestPlanValidation(WorkspaceCase):
    def test_missing_source(self):
        plan = build_plan(self.info, [("Assets/Tex/nope.png", "Assets/Tex/x.png")])
        self.assertTrue(plan.errors)
        self.assertFalse(plan.is_valid)

    def test_existing_destination_is_never_overwritten(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png")])
        self.assertTrue(any("绝不覆盖" in error for error in plan.errors))

    def test_invalid_characters(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/bad?name.png")])
        self.assertTrue(any("非法字符" in error for error in plan.errors))

    def test_outside_assets_is_rejected(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "../evil.png")])
        self.assertTrue(plan.errors)

    def test_folder_move_plan_is_valid(self):
        plan = build_plan(self.info, [("Assets/Tex", "Assets/Art/Textures")])
        self.assertFalse(plan.errors, plan.errors)
        op = plan.operations[0]
        self.assertTrue(op.is_dir)
        self.assertEqual(op.kind, "move_dir")
        self.assertEqual(op.from_meta_rel, "Assets/Tex.meta")
        self.assertEqual(op.to_meta_rel, "Assets/Art/Textures.meta")
        self.assertEqual(op.file_count, 6)  # 3 个资源 + 3 个 .meta
        self.assertTrue(op.tree_hash)

    def test_folder_into_itself_rejected(self):
        plan = build_plan(self.info, [("Assets/Tex", "Assets/Tex/Inner")])
        self.assertTrue(any("自己里面" in error for error in plan.errors))

    def test_overlapping_folder_and_file_ops_rejected(self):
        plan = build_plan(self.info, [("Assets/Tex", "Assets/Art/Textures"),
                                      ("Assets/Tex/a.png", "Assets/a.png")])
        self.assertTrue(any("操作重叠" in error for error in plan.errors))

    def test_swap_is_planned_with_one_temp_hop(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/a.png")])
        self.assertFalse(plan.errors, plan.errors)
        self.assertEqual(len(plan.operations), 3)
        self.assertEqual(sum(1 for op in plan.operations if op.is_temp), 1)
        # 临时步骤必须排在最前面，否则第二步会覆盖第一步
        self.assertTrue(plan.operations[0].is_temp)
        self.assertIn(".ugswap-", plan.operations[0].to_rel)

    def test_chain_is_ordered_without_temp(self):
        # a→b、b→d（d 不存在）：必须先把 b 腾到 d，再把 a 放到 b
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/d.png")])
        self.assertFalse(plan.errors, plan.errors)
        self.assertEqual(len(plan.operations), 2)
        self.assertFalse(any(op.is_temp for op in plan.operations))
        self.assertEqual(plan.operations[0].to_rel, "Assets/Tex/d.png")
        self.assertEqual(plan.operations[1].from_rel, "Assets/Tex/a.png")

    def test_chain_executes_correctly(self):
        b_before = (self.root / "Assets/Tex/b.png").read_bytes()
        a_before = (self.root / "Assets/Tex/a.png").read_bytes()
        plan = build_plan(self.info, [("Assets/Tex/b.png", "Assets/Tex/d.png"),
                                      ("Assets/Tex/a.png", "Assets/Tex/b.png")])
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        self.assertEqual((self.root / "Assets/Tex/b.png").read_bytes(), a_before)
        self.assertEqual((self.root / "Assets/Tex/d.png").read_bytes(), b_before)

    def test_missing_meta_is_a_warning_not_an_error(self):
        (self.root / "Assets" / "Tex" / "lonely.png").write_bytes(b"x")
        plan = build_plan(self.info, [("Assets/Tex/lonely.png", "Assets/Tex/lonely2.png")])
        self.assertFalse(plan.errors)
        self.assertTrue(any("没有 .meta" in message for message in plan.warnings))

    def test_plan_roundtrip_keeps_hash(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png")])
        path = save_plan(plan, self.work / "plan.json")
        again = load_plan(path)
        self.assertEqual(plan.digest(), again.digest())

    def test_leaving_resources_is_warned(self):
        res = self.root / "Assets" / "Resources"
        res.mkdir()
        (res / "logo.png").write_bytes(b"png")
        plan = build_plan(self.info, [("Assets/Resources/logo.png",
                                       "Assets/Tex/logo.png")])
        self.assertFalse(plan.errors)
        self.assertTrue(any("Resources.Load" in message for message in plan.warnings))

    def test_category_rule_skips_resources(self):
        from unityguard.plan import build_category_plan
        from unityguard.project import build_assets

        res = self.root / "Assets" / "Resources"
        res.mkdir()
        (res / "logo.png").write_bytes(b"png")
        plan = build_category_plan(self.info, build_assets(self.info))
        self.assertFalse(any("Resources/" in op.from_rel for op in plan.operations))


class TestApply(WorkspaceCase):
    def test_dry_run_touches_nothing(self):
        before = tree(self.root)
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png")])
        result = apply_plan(self.info, plan, dry_run=True, tier="pro")
        self.assertTrue(result.ok)
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.root / ".unityguard").exists())

    def test_apply_moves_meta_and_keeps_guid(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png")])
        guid = plan.operations[0].guid
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        self.assertFalse((self.root / "Assets/Tex/a.png").exists())
        self.assertTrue((self.root / "Assets/Tex/a2.png").exists())
        meta = self.root / "Assets/Tex/a2.png.meta"
        self.assertTrue(meta.is_file())
        self.assertEqual(parse_meta(meta, "meta").guid, guid)

    def test_second_run_is_idempotent(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png")])
        apply_plan(self.info, plan, tier="pro")
        again = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(again.ok)
        self.assertEqual(again.applied, 0)

    def test_unity_lock_blocks_apply(self):
        (self.root / "Temp").mkdir()
        (self.root / "Temp" / "UnityLockfile").write_bytes(b"")
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png")])
        result = apply_plan(self.info, plan, tier="pro")
        self.assertFalse(result.ok)
        self.assertIn("Unity 编辑器正打开", " ".join(result.failed))
        self.assertTrue((self.root / "Assets/Tex/a.png").exists())

    def test_changed_source_aborts_before_any_move(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/b2.png")])
        (self.root / "Assets/Tex/a.png").write_bytes(b"changed after planning")
        result = apply_plan(self.info, plan, tier="pro")
        self.assertFalse(result.ok)
        self.assertTrue((self.root / "Assets/Tex/a.png").exists())
        self.assertTrue((self.root / "Assets/Tex/b.png").exists())

    def test_moves_into_new_folder_and_cleans_up_on_rollback(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Raw/Textures/a.png")])
        before = tree(self.root)
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue((self.root / "Assets/Raw/Textures/a.png").is_file())
        rollback_journal(Path(result.journal_path))
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.root / "Assets/Raw").exists())


class TestSwaps(WorkspaceCase):
    def _contents(self):
        return {name: (self.root / "Assets" / "Tex" / name).read_bytes()
                for name in ("a.png", "b.png", "c.png")}

    def test_swap_moves_content_and_keeps_guid(self):
        before = self._contents()
        guids = {name: parse_meta(self.root / f"Assets/Tex/{name}.meta", "m").guid
                 for name in ("a.png", "b.png")}
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/a.png")])
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        after = self._contents()
        self.assertEqual(after["b.png"], before["a.png"])
        self.assertEqual(after["a.png"], before["b.png"])
        # GUID 跟着文件走：a 的 guid 现在在 b 的 .meta 里
        self.assertEqual(parse_meta(self.root / "Assets/Tex/b.png.meta", "m").guid,
                         guids["a.png"])
        self.assertEqual(parse_meta(self.root / "Assets/Tex/a.png.meta", "m").guid,
                         guids["b.png"])
        self.assertFalse(list((self.root / "Assets").rglob("*ugswap*")))

    def test_swap_is_idempotent(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/a.png")])
        apply_plan(self.info, plan, tier="pro")
        again = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(again.ok, again.failed)
        self.assertEqual(again.applied, 0)

    def test_swap_rollback_restores(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in (self.root / "Assets").rglob("*") if path.is_file()}
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/a.png")])
        result = apply_plan(self.info, plan, tier="pro")
        rollback = rollback_journal(Path(result.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in (self.root / "Assets").rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_three_way_cycle(self):
        before = self._contents()
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/c.png"),
                                      ("Assets/Tex/c.png", "Assets/Tex/a.png")])
        self.assertFalse(plan.errors, plan.errors)
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        after = self._contents()
        self.assertEqual(after["b.png"], before["a.png"])
        self.assertEqual(after["c.png"], before["b.png"])
        self.assertEqual(after["a.png"], before["c.png"])

    def test_interrupted_swap_refuses_rerun_and_rolls_back(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in (self.root / "Assets").rglob("*") if path.is_file()}
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/b.png"),
                                      ("Assets/Tex/b.png", "Assets/Tex/a.png")])
        # 只做第一步（临时名）后“崩溃”
        first = apply_plan(self.info, plan, tier="pro", fail_after=1)
        self.assertFalse(first.ok)

        retry = apply_plan(self.info, plan, tier="pro")
        self.assertFalse(retry.ok)
        self.assertIn("没有做完", " ".join(retry.failed))

        rollback = rollback_journal(Path(first.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in (self.root / "Assets").rglob("*") if path.is_file()}
        self.assertEqual(after, before)


class TestFolderOperations(WorkspaceCase):
    def test_folder_move_keeps_tree_meta_and_guid(self):
        before = tree(self.root)
        plan = build_plan(self.info, [("Assets/Tex", "Assets/Art/Textures")])
        folder_guid = plan.operations[0].guid
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        self.assertFalse((self.root / "Assets/Tex").exists())
        moved = self.root / "Assets/Art/Textures"
        self.assertTrue((moved / "a.png").is_file())
        self.assertTrue((moved / "a.png.meta").is_file())
        self.assertTrue((moved / "c.png").is_file())
        # 目录 .meta 跟着走，GUID 不变
        self.assertTrue((self.root / "Assets/Art/Textures.meta").is_file())
        self.assertFalse((self.root / "Assets/Tex.meta").exists())
        self.assertEqual(parse_meta(self.root / "Assets/Art/Textures.meta", "m").guid,
                         folder_guid)
        # 每个文件内容不变
        for rel, digest in before.items():
            if rel.startswith("Assets/Tex/"):
                moved_rel = rel.replace("Assets/Tex/", "Assets/Art/Textures/")
                self.assertEqual(
                    hashlib.sha256((self.root / moved_rel).read_bytes()).hexdigest(), digest)

    def test_new_parent_folders_get_meta_then_disappear_on_rollback(self):
        before = tree(self.root)
        plan = build_plan(self.info, [("Assets/Tex", "Assets/Pack/Art/Textures")])
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        for folder in ("Pack", "Pack/Art"):
            meta = self.root / f"Assets/{folder}.meta"
            self.assertTrue(meta.is_file(), f"{folder}.meta 应自动创建")
            self.assertIn("folderAsset: yes", meta.read_text(encoding="utf-8"))
        self.assertTrue((self.root / "Assets/Pack/Art/Textures/a.png").is_file())

        rollback = rollback_journal(Path(result.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.root / "Assets/Pack").exists())
        self.assertFalse((self.root / "Assets/Pack.meta").exists())

    def test_case_only_folder_rename(self):
        plan = build_plan(self.info, [("Assets/Tex", "Assets/tex")])
        self.assertFalse(plan.errors, plan.errors)
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)
        self.assertTrue((self.root / "Assets/tex" / "a.png").is_file())

    def test_folder_rollback_after_edit_is_refused(self):
        plan = build_plan(self.info, [("Assets/Tex", "Assets/Art/Textures")])
        result = apply_plan(self.info, plan, tier="pro")
        (self.root / "Assets/Art/Textures/a.png").write_bytes(b"edited inside")
        rollback = rollback_journal(Path(result.journal_path))
        self.assertFalse(rollback.ok)
        self.assertIn("文件夹内容已变化", " ".join(rollback.failed))


class TestRollback(WorkspaceCase):
    def test_rollback_restores_bytes(self):
        before = tree(self.root)
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png"),
                                      ("Assets/Audio/s.wav", "Assets/Audio/s2.wav")])
        result = apply_plan(self.info, plan, tier="pro")
        self.assertEqual(result.applied, 2)
        rollback = rollback_journal(Path(result.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        self.assertEqual(tree(self.root), before)

    def test_rollback_refuses_when_target_was_edited(self):
        plan = build_plan(self.info, [("Assets/Tex/a.png", "Assets/Tex/a2.png")])
        result = apply_plan(self.info, plan, tier="pro")
        (self.root / "Assets/Tex/a2.png").write_bytes(b"user edited this")
        rollback = rollback_journal(Path(result.journal_path))
        self.assertFalse(rollback.ok)
        self.assertIn("内容已变化", " ".join(rollback.failed))

    def test_crash_then_rollback(self):
        before = tree(self.root)
        pairs = [(f"Assets/Tex/{name}.png", f"Assets/Tex/{name}2.png")
                 for name in ("a", "b", "c")]
        plan = build_plan(self.info, pairs)
        result = apply_plan(self.info, plan, tier="pro", fail_after=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.applied, 1)
        rollback = rollback_journal(Path(result.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        self.assertEqual(tree(self.root), before)


class TestLicensing(WorkspaceCase):
    def _big_plan(self, count: int):
        pairs = []
        tex = self.root / "Assets" / "Tex"
        for index in range(count):
            name = f"f{index:03d}.png"
            (tex / name).write_bytes(f"data-{index}".encode())
            make_meta(tex / (name + ".meta"), name)
            pairs.append((f"Assets/Tex/{name}", f"Assets/Tex/g{index:03d}.png"))
        return build_plan(self.info, pairs)

    def test_free_edition_has_no_operation_limit(self):
        # 这一版是免费完整版：任何规模的计划都不该被拦。
        plan = self._big_plan(FREE_MAX_OPS + 5)
        self.assertEqual(check_apply(plan, License()), [])

    def test_pro_tier_allows_large_plan(self):
        plan = self._big_plan(FREE_MAX_OPS + 5)
        pro = validate_key(issue_key("pro", "tester"))
        self.assertTrue(pro.is_pro)
        self.assertEqual(check_apply(plan, pro), [])

    def test_free_tier_can_apply_small_plan(self):
        plan = self._big_plan(3)
        self.assertEqual(check_apply(plan, License()), [])
        result = apply_plan(self.info, plan, tier="free")
        self.assertTrue(result.ok, result.failed)

    def test_tampered_key_is_rejected(self):
        key = issue_key("pro", "tester")
        prefix, payload, signature = key.split(".")
        tampered = f"{prefix}.{payload}.{'0' * len(signature)}"
        self.assertFalse(validate_key(tampered).valid)
        self.assertFalse(validate_key("not-a-key").valid)

    def test_env_key_is_loaded(self):
        key = issue_key("pro", "env-user")
        os.environ["UNITYGUARD_LICENSE"] = key
        try:
            license_ = load_license(self.root)
        finally:
            os.environ.pop("UNITYGUARD_LICENSE", None)
        self.assertTrue(license_.is_pro)
        self.assertEqual(license_.holder, "env-user")


if __name__ == "__main__":
    unittest.main(verbosity=2)
