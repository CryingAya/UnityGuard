"""Run the complete workflow on a real project copy and prove it is reversible.

    python tools/public_test.py

For every project it does: read-only audit, then for each rule (fix / category /
dedupe) builds a plan, checks that dry-run changes nothing, applies it, re-audits,
rolls back, and compares the tree byte-for-byte with the starting state.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from unityguard.apply import apply_plan, rollback_journal  # noqa: E402
from unityguard.cli import run_audit  # noqa: E402
from unityguard.dedupe import build_dedupe_plan  # noqa: E402
from unityguard.i18n import set_language  # noqa: E402
from unityguard.metaedit import build_fix_plan  # noqa: E402
from unityguard.plan import build_category_plan  # noqa: E402
from unityguard.scan import scan  # noqa: E402

WORK = ROOT / ".tmp_pubtest"
PROJECTS = [
    ("A-2d-extras", Path.home() / "Desktop" / "测试工程" / "A-2d-extras"),
    ("B-Pathfinding", Path.home() / "Desktop" / "测试工程" / "B-Pathfinding"),
    ("C-sample", ROOT / "sample_project"),
]


@dataclass
class Row:
    project: str
    rule: str
    ops: int
    dry_ok: bool
    apply_ok: bool
    rollback_ok: bool
    restored: bool
    score_after: int | None = None
    note: str = ""


def snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".unityguard/"):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def copy_project(source: Path, name: str) -> Path:
    target = WORK / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    junk = target / ".unityguard"
    if junk.exists():
        shutil.rmtree(junk)
    return target


def run_rules(project: Path, label: str) -> list[Row]:
    rows: list[Row] = []
    state = scan(project)
    before = snapshot(project)

    for rule in ("fix", "category", "dedupe"):
        try:
            if rule == "fix":
                plan = build_fix_plan(state.info, state.assets, state.index)
            elif rule == "dedupe":
                plan = build_dedupe_plan(state)
            else:
                plan = build_category_plan(state.info, state.assets)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            rows.append(Row(label, rule, 0, False, False, False, False, f"build failed: {exc}"))
            continue

        if plan.errors:
            rows.append(Row(label, rule, 0, False, False, False, False,
                            "; ".join(plan.errors[:2])))
            continue
        if not plan.operations:
            rows.append(Row(label, rule, 0, True, True, True, True, "无需处理"))
            continue

        dry = apply_plan(state.info, plan, dry_run=True)
        dry_ok = dry.ok and snapshot(project) == before

        result = apply_plan(state.info, plan, tier="free")
        applied_ok = result.ok and result.applied > 0
        score_after = None
        if applied_ok:
            score_after = run_audit(project)["summary"]["score"]

        rollback_ok = False
        restored = False
        if result.journal_path:
            rolled = rollback_journal(Path(result.journal_path))
            rollback_ok = rolled.ok
            restored = snapshot(project) == before
        rows.append(Row(label, rule, len(plan.operations), dry_ok, applied_ok,
                        rollback_ok, restored, score_after=score_after))
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    set_language("zh")
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir()

    all_rows: list[Row] = []
    audits: list[tuple[str, int, dict, int]] = []

    for label, source in PROJECTS:
        if not source.is_dir():
            print(f"[SKIP] {label}: {source} 不存在")
            continue
        project = copy_project(source, label)
        result = run_audit(project)
        audits.append((label, result["summary"]["assets"], result["summary"]["issue_counts"],
                       result["summary"]["score"]))
        all_rows.extend(run_rules(project, label))

    print("\n=== 体检 ===")
    for label, assets, counts, score in audits:
        print(f"{label:16} 资源 {assets:4}  健康分 {score:3}  "
              f"严重 {counts.get('critical', 0)} / 警告 {counts.get('warn', 0)} "
              f"/ 提示 {counts.get('info', 0)}")

    print("\n=== 计划 → 执行 → 回滚 ===")
    print(f"{'工程':16}{'规则':10}{'操作':>5}  dry-run  执行  回滚  复原  执行后健康分")
    for row in all_rows:
        mark = lambda flag: "  ✓  " if flag else "  ✗  "  # noqa: E731
        after = "-" if row.score_after is None else str(row.score_after)
        print(f"{row.project:16}{row.rule:10}{row.ops:5}"
              f"{mark(row.dry_ok)}{mark(row.apply_ok)}{mark(row.rollback_ok)}"
              f"{mark(row.restored)}     {after:>3} {row.note}")

    failures = [row for row in all_rows
                if not (row.dry_ok and row.apply_ok and row.rollback_ok and row.restored)]
    print()
    if failures:
        print(f"结论：{len(failures)} 项失败")
        for row in failures:
            print(f"  - {row.project}/{row.rule}: {row.note}")
        return 1
    print(f"结论：{len(all_rows)} 组合全部通过（含字节级回滚校验）")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
