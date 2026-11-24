"""Generate unityguard/i18n_issues.py from the extracted strings + translations."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from _en_details import EN  # noqa: E402

HEADER = '''"""English copy for audit findings.

The engine writes findings in Chinese; this table supplies the English text.
Keys are the exact Chinese strings, so a missing entry simply falls back to the
Chinese sentence instead of showing a broken placeholder. Keep both sides in
sync when adding a new finding - `tests/test_i18n.py` fails if a finding has no
English text.
"""

from __future__ import annotations

from .i18n import is_english

_PAIRS: list[tuple[str, str]] = [
'''

FOOTER = ''']

EN: dict[str, str] = {zh: en for zh, en in _PAIRS}


def localize(text: str) -> str:
    """Translate a finding's detail/hint when the UI is in English."""
    if not text or not is_english():
        return text
    return EN.get(text, text)
'''


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    items = json.loads((ROOT / "tools" / "_detail_strings.json").read_text(encoding="utf-8"))
    if len(items) != len(EN):
        print(f"count mismatch: {len(items)} Chinese vs {len(EN)} English", file=sys.stderr)
        return 1
    body = []
    for zh, en in zip(items, EN):
        zh_literal = json.dumps(zh, ensure_ascii=False)
        en_literal = json.dumps(en, ensure_ascii=False)
        body.append(f"    ({zh_literal},\n     {en_literal}),\n")
    target = ROOT / "unityguard" / "i18n_issues.py"
    target.write_text(HEADER + "".join(body) + FOOTER, encoding="utf-8")
    print(f"wrote {target} with {len(items)} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
