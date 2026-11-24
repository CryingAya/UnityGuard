"""Tests for UnityGuard.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("UNITYGUARD_LANG", "zh")

from unityguard.cli import run_audit  # noqa: E402
from unityguard.hygiene import check_duplicates, check_paths  # noqa: E402
from unityguard.media import guess_texture_purpose, image_dimensions, wav_info  # noqa: E402
from unityguard.metainfo import parse_meta  # noqa: E402
from unityguard.model import Asset  # noqa: E402
from unityguard.project import ProjectInfo  # noqa: E402


def _load_generator():
    path = ROOT / "tools" / "make_sample_project.py"
    spec = importlib.util.spec_from_file_location("make_sample_project", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


@contextlib.contextmanager
def workspace_tmp():
    """Temp dir created with mkdir (inside the workspace sandbox), not tempfile."""
    base = ROOT / ".tmp_tests"
    base.mkdir(exist_ok=True)
    path = base / uuid.uuid4().hex[:10]
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def make_project(directory: Path, *flags: str) -> Path:
    """Generate the fixture in-process (no subprocess, no argv patching)."""
    argv = sys.argv
    sys.argv = ["make_sample_project.py", str(directory), *flags]
    try:
        GENERATOR.main()
    finally:
        sys.argv = argv
    return directory


def codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def issue(result: dict, code: str) -> dict:
    for item in result["issues"]:
        if item["code"] == code:
            return item
    raise AssertionError(f"missing issue {code}; got {sorted(codes(result))}")


class TestSampleProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_ctx = workspace_tmp()
        cls._tmp = cls._tmp_ctx.__enter__()
        cls.project = make_project(cls._tmp / "project")
        cls.result = run_audit(cls.project)

    @classmethod
    def tearDownClass(cls):
        cls._tmp_ctx.__exit__(None, None, None)

    def test_project_detected(self):
        project = self.result["project"]
        self.assertTrue(project["is_unity"])
        self.assertEqual(project["unity_version"], "2022.3.20f1")

    def test_meta_and_reference_checks(self):
        expected = {
            "meta.duplicate_guid", "meta.missing", "meta.orphan", "meta.folder_meta",
            "ref.dangling", "ref.never_referenced", "ref.unreachable",
            "ref.dead_scripts",
        }
        self.assertTrue(expected <= codes(self.result),
                        f"missing: {expected - codes(self.result)}")

    def test_importer_checks(self):
        expected = {
            "tex.srgb_on_linear", "tex.normal_wrong_type", "tex.oversized",
            "model.readable", "audio.pcm_bgm", "audio.decompress_bgm",
            "audio.short_streaming",
        }
        self.assertTrue(expected <= codes(self.result),
                        f"missing: {expected - codes(self.result)}")

    def test_platform_and_naming_checks(self):
        expected = {"path.long", "path.nfd", "name.version_noise",
                    "name.inconsistent_style", "path.non_ascii", "dup.content"}
        self.assertTrue(expected <= codes(self.result),
                        f"missing: {expected - codes(self.result)}")

    def test_referenced_assets_are_not_reported_dead(self):
        dead = set(issue(self.result, "ref.never_referenced")["paths"])
        for rel in (
            "Assets/Art/Textures/hero_basecolor.png",
            "Assets/Art/Textures/hero_normal.png",
            "Assets/Materials/hero.mat",
            "Assets/Prefabs/hero.prefab",
            "Assets/Scenes/Main.unity",
            "Assets/Scripts/PlayerController.cs",
            "Assets/Shaders/toon.shader",
            "Assets/Resources/logo.png",
            "Assets/Audio/bgm_theme.wav",
            "Assets/Models/prop_crate.fbx",
        ):
            self.assertNotIn(rel, dead, f"{rel} is reachable but was flagged as unused")

    def test_shader_include_is_not_flagged(self):
        self.assertFalse(
            any("ToonCommon.cginc" in path
                for item in self.result["issues"] for path in item["paths"])
        )

    def test_resources_asset_is_not_flagged(self):
        self.assertNotIn(
            "Assets/Resources/logo.png",
            issue(self.result, "ref.never_referenced")["paths"],
        )

    def test_unity_ignored_names_do_not_require_meta(self):
        # .DS_Store, Hidden~/ and Samples~ are invisible to Unity.
        missing = issue(self.result, "meta.missing")["paths"]
        for path in missing:
            self.assertNotIn(".DS_Store", path)
            self.assertNotIn("Hidden~", path)
            self.assertNotIn("Samples~", path)

    def test_shader_referenced_by_declared_name(self):
        # RimLight.shader declares Shader "Custom/RimGlow" and is only used via
        # Shader.Find, so it must land in the code-string bucket, not in "dead".
        self.assertIn("Assets/Shaders/RimLight.shader",
                      " ".join(issue(self.result, "ref.code_string")["paths"]))
        self.assertNotIn("Assets/Shaders/RimLight.shader",
                         issue(self.result, "ref.never_referenced")["paths"])

    def test_score_is_bounded(self):
        self.assertGreaterEqual(self.result["summary"]["score"], 0)
        self.assertLessEqual(self.result["summary"]["score"], 100)

    def test_json_roundtrip(self):
        payload = json.loads(json.dumps(self.result, ensure_ascii=False))
        self.assertEqual(payload["schema"], "unityguard.audit/1")
        self.assertEqual(payload["mode"], "read-only")


class TestBinarySerialization(unittest.TestCase):
    """Unity 5.x binary scenes must disable reachability instead of reporting
    every asset as unused (this was a real false-positive found on a live
    project)."""

    def test_binary_project_refuses_unused_claims(self):
        with workspace_tmp() as tmp:
            project = make_project(tmp / "p")
            scene = project / "Assets" / "Scenes" / "Binary.unity"
            scene.write_bytes(b"\x00\x01\x00\x00\x00\x0b\x00\x00\x00" + b"\xff" * 512)
            scene.with_suffix(".unity.meta").write_text(
                "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n"
                "DefaultImporter:\n  userData: \n", encoding="utf-8")
            result = run_audit(project)
            self.assertFalse(result["extra"]["reference_analysis_usable"])
            self.assertGreater(result["extra"]["binary_serialized_assets"], 0)
            found = issue(result, "project.binary_serialization")
            self.assertEqual(found["severity"], "warn")
            self.assertNotIn("ref.never_referenced", codes(result))
            self.assertNotIn("ref.dead_scripts", codes(result))


class TestProjectFlags(unittest.TestCase):
    def test_meta_in_gitignore_is_critical(self):
        with workspace_tmp() as tmp:
            project = make_project(Path(tmp) / "p", "--bad-gitignore")
            result = run_audit(project)
            found = issue(result, "project.meta_ignored")
            self.assertEqual(found["severity"], "critical")

    def test_unity_lockfile_detected(self):
        with workspace_tmp() as tmp:
            project = make_project(Path(tmp) / "p", "--locked")
            result = run_audit(project)
            self.assertTrue(result["project"]["unity_locked"])
            self.assertEqual(issue(result, "project.unity_open")["severity"], "warn")


class TestPathChecks(unittest.TestCase):
    def _info(self, root: Path) -> ProjectInfo:
        return ProjectInfo(root=root, is_unity=True, assets_dir=root / "Assets")

    def _assets(self, names: list[str]) -> list[Asset]:
        out = []
        for name in names:
            out.append(Asset(path=Path(name), rel="Assets/" + name, size=1,
                             mtime=0.0, ext=Path(name).suffix.lower(), category="texture"))
        return out

    def test_case_collision(self):
        info = self._info(Path("C:/p"))
        found = check_paths(info, self._assets(["Sword.png", "sword.png"]))
        self.assertIn("path.case_collision", {item.code for item in found})

    def test_reserved_and_invalid_names(self):
        info = self._info(Path("C:/p"))
        found = check_paths(info, self._assets(["CON.png", "bad?name.png"]))
        found_codes = {item.code for item in found}
        self.assertIn("path.reserved_name", found_codes)
        self.assertIn("path.invalid_chars", found_codes)

    def test_trailing_dot(self):
        info = self._info(Path("C:/p"))
        found = check_paths(info, self._assets(["weird.png."]))
        self.assertIn("path.trailing_dot_space", {item.code for item in found})

    def test_hard_path_limit(self):
        info = self._info(Path("C:/" + "x" * 200))
        long_rel = "Assets/" + "/".join(["dir"] * 20) + "/file.png"
        found = check_paths(info, self._assets([]) + [
            Asset(path=Path("file.png"), rel=long_rel, size=1, mtime=0.0,
                  ext=".png", category="texture")
        ])
        self.assertIn("path.too_long", {item.code for item in found})


class TestMediaProbes(unittest.TestCase):
    def test_png_dimensions(self):
        with workspace_tmp() as tmp:
            path = Path(tmp) / "a.png"
            GENERATOR.write_png(path, 37, 19, (1, 2, 3))
            self.assertEqual(image_dimensions(path), (37, 19))

    def test_wav_duration(self):
        with workspace_tmp() as tmp:
            path = Path(tmp) / "a.wav"
            GENERATOR.write_wav(path, 2.0, rate=8000)
            info = wav_info(path)
            self.assertIsNotNone(info)
            assert info is not None
            self.assertAlmostEqual(float(info["duration"]), 2.0, places=1)

    def test_texture_purpose_guess(self):
        self.assertEqual(guess_texture_purpose("hero_normal"), "normal")
        self.assertEqual(guess_texture_purpose("T_Rock_BaseColor"), "albedo")
        self.assertEqual(guess_texture_purpose("ui_icon_coin"), "ui")
        self.assertIsNone(guess_texture_purpose("whatever"))

    def test_duplicate_content(self):
        with workspace_tmp() as tmp:
            root = Path(tmp)
            (root / "a.bin").write_bytes(b"same-bytes")
            (root / "b.bin").write_bytes(b"same-bytes")
            (root / "c.bin").write_bytes(b"different")
            assets = [
                Asset(path=root / name, rel="Assets/" + name, size=(root / name).stat().st_size,
                      mtime=0.0, ext=".bin", category="other")
                for name in ("a.bin", "b.bin", "c.bin")
            ]
            found = check_duplicates(assets)
            self.assertEqual(len(found), 1)
            self.assertEqual(len(found[0].paths), 2)


class TestMetaParsing(unittest.TestCase):
    def test_nested_importer_props(self):
        content = """fileFormatVersion: 2
guid: 0123456789abcdef0123456789abcdef
TextureImporter:
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    sRGBTexture: 1
  isReadable: 0
  textureType: 1
  maxTextureSize: 2048
  userData: 
"""
        with workspace_tmp() as tmp:
            path = Path(tmp) / "x.png.meta"
            path.write_text(content, encoding="utf-8")
            meta = parse_meta(path, "Assets/x.png.meta")
        self.assertEqual(meta.guid, "0123456789abcdef0123456789abcdef")
        self.assertEqual(meta.importer, "TextureImporter")
        self.assertEqual(meta.get_int("TextureImporter.mipmaps.sRGBTexture"), 1)
        self.assertEqual(meta.get_int("TextureImporter.textureType"), 1)
        self.assertEqual(meta.get("TextureImporter.userData"), "")


class TestReadOnlyGuarantee(unittest.TestCase):
    """The audit must not modify the audited tree."""

    def test_tree_unchanged(self):
        with workspace_tmp() as tmp:
            project = make_project(Path(tmp) / "p")
            before = {
                path.relative_to(project).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in project.rglob("*") if path.is_file()
            }
            run_audit(project)
            after = {
                path.relative_to(project).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in project.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
