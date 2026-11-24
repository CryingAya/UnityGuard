"""English mode must not leak Chinese into the report.

The engine builds findings in Chinese; `i18n_issues.localize()` swaps in the
English text at render time. This test is the safety net for both halves: every
finding that shows up on the sample project must be fully English, and every
Chinese detail string in the source must have a translation entry.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unityguard import i18n  # noqa: E402
from unityguard.cli import run_audit  # noqa: E402
from unityguard.i18n_issues import EN  # noqa: E402

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "make_sample_project", ROOT / "tools" / "make_sample_project.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestEnglishReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        import uuid

        base = ROOT / ".tmp_tests"
        base.mkdir(exist_ok=True)
        cls.work = base / uuid.uuid4().hex[:10]
        cls.work.mkdir()
        cls.project = cls.work / "p"
        argv = sys.argv
        sys.argv = ["make_sample_project.py", str(cls.project)]
        try:
            _load_generator().main()
        finally:
            sys.argv = argv

        previous = os.environ.get("UNITYGUARD_LANG")
        os.environ["UNITYGUARD_LANG"] = "en"
        i18n.set_language("en")
        try:
            cls.result = run_audit(cls.project)
        finally:
            i18n.set_language("zh")
            if previous is None:
                os.environ.pop("UNITYGUARD_LANG", None)
            else:
                os.environ["UNITYGUARD_LANG"] = previous
        cls.cleanup = lambda: shutil.rmtree(cls.work, ignore_errors=True)

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls.work, ignore_errors=True)

    def test_details_and_hints_are_english(self):
        offenders = [
            (issue["code"], field, issue[field][:60])
            for issue in self.result["issues"]
            for field in ("detail", "hint")
            if issue.get(field) and _CJK.search(issue[field])
        ]
        self.assertFalse(offenders, f"Chinese left in English mode: {offenders[:5]}")

    def test_titles_are_english(self):
        offenders = [(issue["code"], issue["title"]) for issue in self.result["issues"]
                     if _CJK.search(issue["title"])]
        self.assertFalse(offenders, f"Chinese titles: {offenders[:5]}")

    def test_every_source_string_has_a_translation(self):
        """Walk the source for Chinese literals and make sure each is covered."""
        import ast

        missing: list[str] = []
        for name in ("checks.py", "hygiene.py", "media.py"):
            path = ROOT / "unityguard" / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Name) and func.id == "Issue"):
                    continue
                for keyword in node.keywords:
                    if keyword.arg not in ("detail", "hint"):
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                            keyword.value.value, str):
                        text = keyword.value.value
                        if _CJK.search(text) and text not in EN:
                            missing.append(f"{name}:{text[:40]}")
        self.assertFalse(missing, f"untranslated detail/hint: {missing[:5]}")

    def test_chinese_mode_is_unchanged(self):
        i18n.set_language("zh")
        try:
            result = run_audit(self.project)
        finally:
            pass
        self.assertTrue(any(_CJK.search(issue["detail"]) for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
