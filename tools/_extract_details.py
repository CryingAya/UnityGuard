"""One-off helper: collect every literal detail=/hint= string."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

FILES = ("unityguard/checks.py", "unityguard/hygiene.py", "unityguard/media.py")


def collect(lines: list[str], start: int) -> tuple[str, int]:
    buf: list[str] = []
    depth = 0
    index = start
    while index < len(lines):
        line = lines[index]
        buf.append(line)
        in_string = False
        escaped = False
        for char in line:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
        stripped = line.rstrip()
        ends = (stripped.endswith(",") or stripped.endswith(")")
                or stripped.endswith("))"))
        if depth <= 0 and ends:
            break
        index += 1
    return "\n".join(buf), index


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    values: dict[str, int] = {}
    manual: list[tuple[str, str, str]] = []
    for rel in FILES:
        lines = Path(rel).read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            match = re.match(r"^\s*(detail|hint)=(.*)$", lines[index])
            if not match:
                index += 1
                continue
            block, end = collect(lines, index)
            index = end + 1
            expr = block.split("=", 1)[1].strip()
            expr = re.sub(r"\)+\s*$", "", expr).rstrip().rstrip(",")
            try:
                node = ast.parse("(" + expr + ")", mode="eval").body
            except SyntaxError:
                manual.append((rel, match.group(1), expr[:70]))
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                values[node.value] = values.get(node.value, 0) + 1
            else:
                manual.append((rel, match.group(1), expr[:70]))

    print(f"literal strings: {len(values)}")
    print(f"needs manual   : {len(manual)}")
    for item in manual:
        print("  ", item)
    Path("tools/_detail_strings.json").write_text(
        json.dumps(sorted(values), ensure_ascii=False, indent=1), encoding="utf-8")
    print("dumped tools/_detail_strings.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
