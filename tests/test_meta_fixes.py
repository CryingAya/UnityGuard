"""Tests for the one-click importer fixes (`plan --rule fix`).

These cover the risky part: rewriting a `.meta` in place without disturbing the
guid, the formatting, or any line we did not mean to touch.
"""

from __future__ import annotations

import hashlib
import importlib.util
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
from unityguard.metaedit import build_fix_plan, edit_meta_text, Edit  # noqa: E402
from unityguard.metainfo import build_index, parse_meta  # noqa: E402
from unityguard.plan import load_plan, save_plan  # noqa: E402
from unityguard.project import build_assets, locate  # noqa: E402


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "make_sample_project", ROOT / "tools" / "make_sample_project.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


def _guid(seed: str) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def write_meta(path: Path, seed: str, body: str, newline: str = "\n") -> None:
    text = "\n".join([
        "fileFormatVersion: 2",
        f"guid: {_guid(seed)}",
        body,
    ])
    if newline != "\n":
        text = text.replace("\n", newline)
    path.write_bytes(text.encode("utf-8"))


TEX_BODY = """TextureImporter:
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    enableMipMap: 1
    sRGBTexture: {srgb}
  isReadable: 0
  maxTextureSize: {max_size}
  textureType: {tex_type}
"""

AUDIO_BODY = """AudioImporter:
  serializedVersion: 7
  defaultSettings:
    serializedVersion: 2
    loadType: {load_type}
    compressionFormat: {compression}
  forceToMono: 0
"""

MODEL_BODY = """ModelImporter:
  serializedVersion: 22200
  animations:
    clipAnimations: []
    isReadable: {readable}
  importMaterials: 1
"""


def tiny_fix_project(base: Path) -> Path:
    root = base / "p"
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.1f1\n", encoding="utf-8")

    tex = root / "Assets" / "Tex"
    tex.mkdir(parents=True)
    (tex / "hero_normal.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"n" * 32)
    write_meta(tex / "hero_normal.png.meta", "hero_normal",
               TEX_BODY.format(srgb=1, max_size=2048, tex_type=0))

    (tex / "hero_roughness.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"r" * 32)
    write_meta(tex / "hero_roughness.png.meta", "hero_roughness",
               TEX_BODY.format(srgb=1, max_size=4096, tex_type=0))

    (tex / "hero_albedo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"a" * 32)
    write_meta(tex / "hero_albedo.png.meta", "hero_albedo",
               TEX_BODY.format(srgb=1, max_size=2048, tex_type=0))  # 正确：颜色图

    audio = root / "Assets" / "Audio"
    audio.mkdir()
    GENERATOR.write_wav(audio / "bgm.wav", 12.0, rate=8000)
    write_meta(audio / "bgm.wav.meta", "bgm",
               AUDIO_BODY.format(load_type=0, compression=0))

    GENERATOR.write_wav(audio / "click.wav", 0.2, rate=8000)
    write_meta(audio / "click.wav.meta", "click",
               AUDIO_BODY.format(load_type=2, compression=1))

    models = root / "Assets" / "Models"
    models.mkdir()
    (models / "crate.fbx").write_bytes(b";; fake fbx\n")
    write_meta(models / "crate.fbx.meta", "crate", MODEL_BODY.format(readable=1))
    return root


class FixCase(unittest.TestCase):
    def setUp(self) -> None:
        base = ROOT / ".tmp_tests"
        base.mkdir(exist_ok=True)
        self.work = base / uuid.uuid4().hex[:10]
        self.work.mkdir()
        self.root = tiny_fix_project(self.work)
        self.info = locate(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def fix_plan(self, **kwargs):
        assets = build_assets(self.info)
        index = build_index(self.info, assets)
        return build_fix_plan(self.info, assets, index, **kwargs)

    def meta_text(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8", newline="")


class TestProposals(FixCase):
    def test_detects_all_expected_edits(self):
        plan = self.fix_plan()
        targets = {op.from_rel: op.edits for op in plan.operations}
        self.assertIn("Assets/Tex/hero_normal.png", targets)
        normal_edits = {edit["key"]: edit for edit in targets["Assets/Tex/hero_normal.png"]}
        self.assertEqual(normal_edits["textureType"]["new"], "1")
        self.assertEqual(normal_edits["sRGBTexture"]["new"], "0")
        self.assertIn("Assets/Tex/hero_roughness.png", targets)
        self.assertIn("Assets/Audio/bgm.wav", targets)
        self.assertIn("Assets/Audio/click.wav", targets)
        self.assertIn("Assets/Models/crate.fbx", targets)

    def test_correct_albedo_is_left_alone(self):
        plan = self.fix_plan()
        self.assertNotIn("Assets/Tex/hero_albedo.png",
                         [op.from_rel for op in plan.operations])

    def test_cap_texture_size_is_opt_in(self):
        plan = self.fix_plan(cap_texture_size=1024)
        roughness = next(op for op in plan.operations
                         if op.from_rel == "Assets/Tex/hero_roughness.png")
        keys = {edit["key"]: edit for edit in roughness.edits}
        self.assertEqual(keys["maxTextureSize"]["new"], "1024")

    def test_plan_roundtrip_keeps_edits(self):
        plan = self.fix_plan()
        path = save_plan(plan, self.work / "plan.json")
        again = load_plan(path)
        self.assertEqual(plan.digest(), again.digest())
        self.assertTrue(any(op.edits for op in again.operations))


class TestTextEditing(FixCase):
    def test_only_target_lines_change(self):
        before = self.meta_text("Assets/Tex/hero_normal.png.meta")
        plan = self.fix_plan()
        apply_plan(self.info, plan, tier="pro")
        after = self.meta_text("Assets/Tex/hero_normal.png.meta")
        self.assertNotEqual(before, after)
        before_lines, after_lines = before.splitlines(), after.splitlines()
        self.assertEqual(len(before_lines), len(after_lines))
        changed = [(a, b) for a, b in zip(before_lines, after_lines) if a != b]
        self.assertEqual(len(changed), 2)
        for old, new in changed:
            self.assertTrue(old.strip().startswith(("sRGBTexture", "textureType")), old)

    def test_guid_is_preserved(self):
        before_guid = parse_meta(self.root / "Assets/Tex/hero_normal.png.meta", "m").guid
        plan = self.fix_plan()
        apply_plan(self.info, plan, tier="pro")
        after_guid = parse_meta(self.root / "Assets/Tex/hero_normal.png.meta", "m").guid
        self.assertEqual(before_guid, after_guid)

    def test_crlf_line_endings_survive(self):
        meta = self.root / "Assets/Tex/hero_normal.png.meta"
        write_meta(meta, "hero_normal", TEX_BODY.format(srgb=1, max_size=2048, tex_type=0),
                   newline="\r\n")
        plan = self.fix_plan()
        apply_plan(self.info, plan, tier="pro")
        raw = meta.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn(b"sRGBTexture: 0\r\n", raw)

    def test_edit_text_reports_conflict(self):
        text = "fileFormatVersion: 2\nguid: abc\nTextureImporter:\n  sRGBTexture: 0\n"
        _, applied, problems = edit_meta_text(text, [Edit("sRGBTexture", "1", "0", "x")])
        self.assertFalse(applied)
        self.assertTrue(problems)

    def test_missing_key_is_reported(self):
        text = "fileFormatVersion: 2\nguid: abc\nTextureImporter:\n  isReadable: 0\n"
        _, applied, problems = edit_meta_text(text, [Edit("sRGBTexture", "1", "0", "x")])
        self.assertFalse(applied)
        self.assertIn("找不到键", problems[0])


class TestFixEndToEnd(FixCase):
    def test_audit_issues_disappear(self):
        before = run_audit(self.root)
        codes_before = {issue["code"] for issue in before["issues"]}
        self.assertIn("tex.srgb_on_linear", codes_before)
        self.assertIn("tex.normal_wrong_type", codes_before)

        plan = self.fix_plan()
        result = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(result.ok, result.failed)

        after = run_audit(self.root)
        codes_after = {issue["code"] for issue in after["issues"]}
        self.assertNotIn("tex.srgb_on_linear", codes_after)
        self.assertNotIn("tex.normal_wrong_type", codes_after)
        self.assertNotIn("audio.pcm_bgm", codes_after)
        self.assertNotIn("audio.decompress_bgm", codes_after)
        self.assertNotIn("audio.short_streaming", codes_after)
        self.assertNotIn("model.readable", codes_after)
        self.assertGreater(after["summary"]["score"], before["summary"]["score"])

    def test_replan_after_fix_is_empty(self):
        plan = self.fix_plan()
        apply_plan(self.info, plan, tier="pro")
        self.assertEqual(len(self.fix_plan().operations), 0)

    def test_same_plan_twice_is_idempotent(self):
        plan = self.fix_plan()
        apply_plan(self.info, plan, tier="pro")
        again = apply_plan(self.info, plan, tier="pro")
        self.assertTrue(again.ok)
        self.assertEqual(again.applied, 0)

    def test_rollback_restores_bytes(self):
        def snap():
            return {
                path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*.meta")
                if ".unityguard" not in path.parts
            }

        before = snap()
        plan = self.fix_plan()
        result = apply_plan(self.info, plan, tier="pro")
        rollback = rollback_journal(Path(result.journal_path))
        self.assertTrue(rollback.ok, rollback.failed)
        self.assertEqual(snap(), before)

    def test_meta_changed_after_plan_aborts_before_writing(self):
        plan = self.fix_plan()
        meta = self.root / "Assets/Tex/hero_normal.png.meta"
        meta.write_text(meta.read_text(encoding="utf-8") + "\n# hand edited\n",
                        encoding="utf-8")
        result = apply_plan(self.info, plan, tier="pro")
        self.assertFalse(result.ok)
        self.assertIn("内容已变化", " ".join(result.failed))

    def test_rollback_refuses_when_meta_edited_after_fix(self):
        plan = self.fix_plan()
        result = apply_plan(self.info, plan, tier="pro")
        meta = self.root / "Assets/Tex/hero_normal.png.meta"
        meta.write_text(meta.read_text(encoding="utf-8") + "\n# edited later\n",
                        encoding="utf-8")
        rollback = rollback_journal(Path(result.journal_path))
        self.assertFalse(rollback.ok)
        self.assertIn("拒绝覆盖", " ".join(rollback.failed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
