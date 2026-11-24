"""Command line entry point.

    unityguard [audit] <project>                  # read-only health report
    unityguard plan <project> --rule fix          # importer fixes
    unityguard plan <project> --rule category     # sort assets by type
    unityguard plan <project> --rule dedupe       # quarantine unused duplicates
    unityguard apply --plan plan.json --dry-run
    unityguard apply --plan plan.json --yes
    unityguard rollback <project> --last
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .apply import apply_plan, rollback_journal
from .checks import (check_meta, check_project, check_references,
                     check_serialization, check_unused)
from .dedupe import build_dedupe_plan
from .hygiene import check_duplicates, check_naming, check_paths
from .i18n import get_language, set_language, tr
from .licensing import (FREE_EDITION, FREE_MAX_OPS, check_apply, issue_key,
                        load_license, save_license_key)
from .media import check_audio, check_models, check_textures
from .metaedit import build_fix_plan
from .metainfo import build_index
from .model import SEVERITY_LABEL, SEVERITY_ORDER, Issue, severity_label
from .plan import (build_category_plan, build_plan, build_plan_from_json,
                   load_plan, save_plan)
from .project import locate
from .report import build_result, write_html, write_json, write_markdown
from .scan import scan

COMMANDS = {"audit", "plan", "apply", "rollback", "license"}

# Inside the packaged exe the program is UnityGuard.exe, not the .py script.
PROG = "UnityGuard.exe" if getattr(sys, "frozen", False) else "unity_asset_audit.py"

_COLOR = {
    "critical": "\033[91m",
    "warn": "\033[93m",
    "info": "\033[96m",
    "ok": "\033[92m",
    "dim": "\033[90m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _c(text: str, key: str) -> str:
    if not _supports_color():
        return text
    return f"{_COLOR.get(key, '')}{text}{_COLOR['reset']}"


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
def run_audit(target: Path, *, skip_duplicates: bool = False) -> dict:
    state = scan(target)
    info, assets, index, graph = state.info, state.assets, state.index, state.graph

    issues: list[Issue] = []
    issues += check_project(info)
    if info.is_unity:
        # .meta / GUID / reachability only make sense inside a real Unity project.
        issues += check_meta(index, assets)
        issues += check_references(graph, assets)
        issues += check_serialization(graph)
        issues += check_unused(graph, assets)
    issues += check_paths(info, assets)
    issues += check_naming(assets)
    issues += check_textures(info, assets, index)
    issues += check_audio(info, assets, index)
    issues += check_models(info, assets, index)
    if not skip_duplicates:
        issues += check_duplicates(assets)

    issues.sort(key=lambda issue: issue.sort_key())
    extra = {
        "metas": len(index.metas),
        "folder_metas": index.folder_metas,
        "duplicate_guid_groups": len(index.duplicate_guids),
        "yaml_files_scanned": graph.scanned_files,
        "binary_serialized_assets": len(graph.binary_assets),
        "reference_analysis_usable": graph.reference_analysis_usable,
        "reachable_assets": len(graph.reachable),
        "skipped_duplicate_hashing": skip_duplicates,
    }
    return build_result(info, assets, issues, extra)


def _default_out(info, target: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parent = info.root.parent if info.root.parent != info.root else info.root
    name = f"unityguard_{info.root.name or 'assets'}_{stamp}"
    return parent / name


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _cmd_audit(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.exists():
        print(tr("路径不存在：{p}", "Path not found: {p}", p=target), file=sys.stderr)
        return 2

    info = locate(target)
    if not info.assets_dir.is_dir():
        print(tr("没有找到素材目录：{p}", "No asset folder found: {p}", p=info.assets_dir),
              file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser() if args.out else _default_out(info, target)
    if info.is_unity and _is_inside(out_dir, info.assets_dir):
        print(tr("拒绝把报告写进 Assets/：Unity 会把它当成资源导入。请换一个输出目录。",
                 "Refusing to write the report inside Assets/ - Unity would import it. "
                 "Pick another output folder."), file=sys.stderr)
        return 2

    formats = [item.strip().lower() for item in args.format.split(",") if item.strip()]
    unknown = set(formats) - {"md", "json", "html"}
    if unknown:
        print(tr("不支持的格式：{x}", "Unsupported format(s): {x}", x=", ".join(sorted(unknown))),
              file=sys.stderr)
        return 2

    result = run_audit(target, skip_duplicates=args.fast)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if "json" in formats:
        path = out_dir / "report.json"
        write_json(result, path)
        written.append(path)
    if "md" in formats:
        path = out_dir / "report.md"
        write_markdown(result, path)
        written.append(path)
    if "html" in formats:
        path = out_dir / "report.html"
        write_html(result, path)
        written.append(path)

    counts = result["summary"]["issue_counts"]
    summary = result["summary"]

    if not args.quiet:
        print()
        print(_c(tr("UnityGuard {v} · 只读体检", "UnityGuard {v} · read-only audit",
                    v=__version__), "bold"))
        print(tr("工程  {root}", "Project  {root}", root=result["project"]["root"])
              + (f"   (Unity {result['project']['unity_version']})"
                 if result["project"]["unity_version"] else ""))
        print(tr("资源  {n} 个 / {size}", "Assets  {n} / {size}",
                 n=summary["assets"], size=_human(summary["total_bytes"])))
        print(tr("健康分  {score} / 100", "Score  {score} / 100",
                 score=_c(str(summary["score"]), "bold"))
              + f"   {_c(tr('严重', 'critical') + ' ' + str(counts.get('critical', 0)), 'critical')}"
              f" · {_c(tr('警告', 'warnings') + ' ' + str(counts.get('warn', 0)), 'warn')}"
              f" · {_c(tr('提示', 'notes') + ' ' + str(counts.get('info', 0)), 'info')}")
        print()
        for severity in ("critical", "warn", "info"):
            bucket = [issue for issue in result["issues"] if issue["severity"] == severity]
            if not bucket:
                continue
            print(_c(severity_label(severity) + tr("：", ":"), severity))
            for issue in bucket[:8]:
                print(f"  - {issue['title']}  {_c('[' + issue['code'] + ']', 'dim')}")
            if len(bucket) > 8:
                print(_c(tr("  … 另有 {n} 项", "  … {n} more", n=len(bucket) - 8), "dim"))
            print()

    for path in written:
        print(tr("报告：{p}", "Report: {p}", p=path))

    if args.fail_on != "none":
        threshold = SEVERITY_ORDER["warn" if args.fail_on == "warn" else "critical"]
        worst = min((SEVERITY_ORDER[issue["severity"]] for issue in result["issues"]),
                    default=99)
        if worst <= threshold:
            return 1
    return 0


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def _parse_pairs(values: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        if "=" not in value:
            raise SystemExit(tr("--rename 需要 旧=新 的形式，收到：{v!r}",
                                "--rename expects OLD=NEW, got: {v!r}", v=value))
        source, target = value.split("=", 1)
        pairs.append((source.strip(), target.strip()))
    return pairs


def _cmd_plan(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if not target.exists():
        print(tr("路径不存在：{p}", "Path not found: {p}", p=target), file=sys.stderr)
        return 2
    info = locate(target)

    if args.map:
        import json

        payload = json.loads(Path(args.map).read_text(encoding="utf-8"))
        plan = build_plan_from_json(info, payload)
    elif args.rule == "fix":
        state = scan(target)
        plan = build_fix_plan(state.info, state.assets, state.index,
                              cap_texture_size=args.cap_texture_size)
    elif args.rule == "dedupe":
        state = scan(target)
        plan = build_dedupe_plan(state)
    elif args.rule == "category":
        state = scan(target)
        plan = build_category_plan(state.info, state.assets)
    elif args.rename:
        plan = build_plan(info, _parse_pairs(args.rename))
    else:
        print(tr("需要 --rename 旧=新、--map 文件.json 或 --rule category|fix|dedupe 之一",
                 "Use --rename OLD=NEW, --map file.json, or --rule category|fix|dedupe"),
              file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser() if args.out else info.root / ".unityguard" / "plans"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = save_plan(plan, out_dir / f"plan_{stamp}.json")

    if not args.quiet:
        real = [op for op in plan.operations if not op.is_temp]
        hops = len(plan.operations) - len(real)
        counts = {
            "rename": sum(1 for op in real if op.kind == "rename"),
            "move": sum(1 for op in real if op.kind == "move"),
            "move_dir": sum(1 for op in real if op.kind == "move_dir"),
            "meta_edit": sum(1 for op in real if op.kind == "meta_edit"),
        }
        print()
        print(_c(tr("计划：{n} 个操作", "Plan: {n} operations", n=len(real)), "bold")
              + tr("（改名 {rename} / 移动 {move} / 文件夹 {dirs} / 修复 {fix}）",
                   " (rename {rename} / move {move} / folders {dirs} / fixes {fix})",
                   rename=counts["rename"], move=counts["move"],
                   dirs=counts["move_dir"], fix=counts["meta_edit"])
              + (tr(" + {n} 个临时步骤", " + {n} temporary steps", n=hops) if hops else ""))
        print(tr("工程  {root}", "Project  {root}", root=plan.project_root))
        print()
        for op in real[:200]:
            if op.kind == "meta_edit":
                changes = ", ".join(f"{e['key']} {e['old']}→{e['new']}" for e in op.edits)
                print(f"  fix    {op.from_rel}  {_c('·', 'dim')}  {changes}")
            else:
                print(f"  {op.kind:8} {op.from_rel}  {_c('→', 'dim')}  {op.to_rel}")
        if len(real) > 200:
            print(_c(tr("  … 另有 {n} 个操作", "  … {n} more operations", n=len(real) - 200),
                     "dim"))
        if hops:
            print(_c(tr("  （名称互换：自动先改临时名 .ugswap-*，再落到目标名）",
                        "  (name swap: parked under .ugswap-* first, then moved into place)"),
                     "dim"))
        print()
        for warning in plan.warnings[:10]:
            print(_c(tr("警告  ", "warn  "), "warn") + warning)
        for error in plan.errors:
            print(_c(tr("错误  ", "error  "), "critical") + error)
        print()
        print(tr("计划文件：{p}", "Plan file: {p}", p=plan_path))
        print(_c(tr("下一步：", "Next: "), "dim")
              + f"{PROG} apply --plan \"{plan_path}\" --dry-run")

    if plan.errors:
        return 2
    if not plan.operations:
        return 2
    return 0


# --------------------------------------------------------------------------- #
# apply / rollback
# --------------------------------------------------------------------------- #
def _cmd_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).expanduser()
    if not plan_path.is_file():
        print(tr("计划文件不存在：{p}", "Plan file not found: {p}", p=plan_path),
              file=sys.stderr)
        return 2
    plan = load_plan(plan_path)
    info = locate(Path(plan.project_root))
    if Path(plan.project_root).resolve() != info.root.resolve():
        print(tr("计划所属工程：{a}，定位到：{b}", "Plan belongs to {a}, located {b}",
                 a=plan.project_root, b=info.root), file=sys.stderr)

    if plan.errors:
        print(tr("计划本身有错误，拒绝执行：", "The plan has errors, refusing to run:"),
              file=sys.stderr)
        for error in plan.errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    license_ = load_license(info.root)
    blocked = check_apply(plan, license_)
    if blocked and not args.dry_run:
        print()
        for reason in blocked:
            print(_c(tr("需要 Pro：", "Pro required: "), "warn") + reason)
        print(tr("当前授权：{label}", "Current licence: {label}", label=license_.label()))
        print(_c(tr("提示：", "Hint: "), "dim")
              + tr("PLAN / AUDIT / ROLLBACK 全都免费；只有「一次执行超过额度」需要 Pro。",
                   "PLAN / AUDIT / ROLLBACK are always free; only running more than the "
                   "free quota in one go needs Pro."))
        return 3

    if not args.dry_run and not args.yes:
        print(tr("这是写盘操作，必须显式确认：", "This writes to disk. Confirm explicitly:"),
              file=sys.stderr)
        print(tr("  预览：{prog} apply --plan ... --dry-run",
                 "  preview: {prog} apply --plan ... --dry-run", prog=PROG), file=sys.stderr)
        print(tr("  执行：{prog} apply --plan ... --yes",
                 "  apply:   {prog} apply --plan ... --yes", prog=PROG), file=sys.stderr)
        return 2

    result = apply_plan(info, plan, dry_run=args.dry_run, force=args.force,
                        tier="pro" if license_.is_pro else "free")

    if args.dry_run:
        print(_c(tr("dry-run：{n} 个操作待执行，未修改任何文件",
                    "dry-run: {n} operations pending, nothing written",
                    n=sum(1 for op in plan.operations if not op.is_temp)), "bold"))
        for op in plan.operations:
            if op.is_temp:
                continue
            print(f"  {op.kind:8} {op.from_rel}  →  {op.to_rel}")
        return 0 if result.ok else 4

    print(_c(tr("{state}：执行 {done} 个，跳过 {skip} 个，GUID 校验 {guids} 个",
                "{state}: {done} applied, {skip} skipped, {guids} GUID checks",
                state=tr("完成", "done") if result.ok else tr("中止", "stopped"),
                done=result.applied, skip=result.skipped, guids=result.guid_checks),
             "ok" if result.ok else "critical"))
    for message in result.messages:
        print(f"  {message}")
    for failure in result.failed[:20]:
        print(_c(tr("  失败  ", "  failed  "), "critical") + failure)
    if result.journal_path:
        print(_c(tr("回滚：", "Rollback: "), "dim")
              + f"{PROG} rollback --journal \"{result.journal_path}\"")
    return 0 if result.ok else 4


def _latest_journal(project) -> Path | None:
    directory = project.root / ".unityguard" / "journals"
    if not directory.is_dir():
        return None
    journals = sorted(directory.glob("rollback_*.json"))
    return journals[-1] if journals else None


def _cmd_rollback(args: argparse.Namespace) -> int:
    if args.journal:
        journal = Path(args.journal).expanduser()
    else:
        info = locate(Path(args.target).expanduser())
        journal = _latest_journal(info)
        if journal is None:
            print(tr("没有找到回滚日志（.unityguard/journals/）",
                     "No rollback journal found (.unityguard/journals/)"), file=sys.stderr)
            return 2
    if not journal.is_file():
        print(tr("日志不存在：{p}", "Journal not found: {p}", p=journal), file=sys.stderr)
        return 2

    result = rollback_journal(journal, dry_run=args.dry_run, force=args.force)
    print(_c(tr("{state}：{done} 个操作，跳过 {skip} 个",
                "{state}: {done} operations, {skip} skipped",
                state=tr("预览", "preview") if args.dry_run else tr("回滚", "rollback"),
                done=result.applied, skip=result.skipped), "bold"))
    for message in result.messages:
        print(f"  {message}")
    for failure in result.failed[:20]:
        print(_c(tr("  失败  ", "  failed  "), "critical") + failure)
    return 0 if result.ok else 4


# --------------------------------------------------------------------------- #
# license
# --------------------------------------------------------------------------- #
def _cmd_license(args: argparse.Namespace) -> int:
    if args.activate:
        from .licensing import validate_key

        candidate = validate_key(args.activate)
        if not candidate.valid:
            print(_c(tr("授权码无效：{why}", "Invalid licence: {why}", why=candidate.source),
                     "critical"), file=sys.stderr)
            return 2
        path = save_license_key(args.activate)
        print(_c(tr("已激活 {label}", "Activated {label}", label=candidate.label()), "ok"))
        print(tr("授权文件：{p}", "Licence file: {p}", p=path))
        return 0

    if args.issue:
        if not os.environ.get("UNITYGUARD_DEV"):
            print(tr("签发授权码需要开发模式：设置 UNITYGUARD_DEV=1 后再试。",
                     "Issuing keys requires developer mode: set UNITYGUARD_DEV=1."),
                  file=sys.stderr)
            return 2
        print(issue_key("pro", holder=args.issue or ""))
        return 0

    license_ = load_license()
    print(tr("版本：{label}", "Edition: {label}", label=_c(license_.label(), "ok")))
    print(tr("来源：{src}", "Source: {src}", src=license_.source))
    if FREE_EDITION:
        print(tr("这一版是免费完整版：体检、计划、执行、回滚、去重全部不限量。",
                 "This build is the complete free edition: audit, plan, apply, rollback "
                 "and dedupe are all unlimited."))
        print(tr("未来会推出付费版（团队批量、编辑器插件），现有功能保持免费。",
                 "A paid edition (team batch tools, editor plugin) may come later; "
                 "everything you have now stays free."))
    else:
        print(tr("免费额度：单次 {n} 个操作（audit / plan / rollback 不限）",
                 "Free quota: {n} operations per run (audit / plan / rollback unlimited)",
                 n=FREE_MAX_OPS))
        print(_c(tr("升级：", "Upgrade: "), "dim")
              + f"{PROG} license --activate <key>")
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unityguard",
        description=tr(
            "Unity 素材体检与安全整理：audit 只读体检 / plan 生成计划 / apply 执行 / rollback 回滚。",
            "Unity asset audit and safe cleanup: audit / plan / apply / rollback."),
    )
    parser.add_argument("--version", action="version", version=f"UnityGuard {__version__}")
    parser.add_argument("--lang", choices=("zh", "en"), help=tr("界面语言", "UI language"))
    sub = parser.add_subparsers(dest="command")

    audit = sub.add_parser("audit", help=tr("只读体检", "read-only audit"))
    audit.add_argument("target", nargs="?", default=".",
                       help=tr("Unity 工程根目录或 Assets 目录", "Unity project root or Assets folder"))
    audit.add_argument("-o", "--out", help=tr("报告输出目录", "report output folder"))
    audit.add_argument("--format", default="md,json,html",
                       help=tr("输出格式：md,json,html", "formats: md,json,html"))
    audit.add_argument("--fast", action="store_true",
                       help=tr("跳过内容重复检测", "skip content de-duplication hashing"))
    audit.add_argument("--fail-on", choices=("none", "warn", "critical"), default="none",
                       help=tr("达到该级别返回非 0（CI 用）",
                               "exit non-zero at this severity (for CI)"))
    audit.add_argument("-q", "--quiet", action="store_true", help=tr("只输出路径", "paths only"))

    plan = sub.add_parser("plan", help=tr("生成变更计划（不写盘）", "build a plan (no writes)"))
    plan.add_argument("target", help=tr("Unity 工程根目录", "Unity project root"))
    plan.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                      help=tr("单个改名/移动，可重复", "one rename/move, repeatable"))
    plan.add_argument("--map", help=tr("批量映射 JSON", "batch mapping JSON"))
    plan.add_argument("--rule", choices=("category", "fix", "dedupe"),
                      help=tr("category=按类型归档，fix=修导入设置，dedupe=去重隔离",
                              "category=sort by type, fix=importer settings, "
                              "dedupe=quarantine duplicates"))
    plan.add_argument("--cap-texture-size", type=int, default=None, metavar="N",
                      help=tr("配合 --rule fix：把非 UI 贴图压到 N",
                              "with --rule fix: cap non-UI textures at N"))
    plan.add_argument("-o", "--out", help=tr("计划输出目录", "plan output folder"))
    plan.add_argument("-q", "--quiet", action="store_true")

    apply = sub.add_parser("apply", help=tr("执行计划（写盘）", "apply a plan (writes)"))
    apply.add_argument("--plan", required=True, help=tr("计划文件", "plan file"))
    apply.add_argument("--dry-run", action="store_true",
                       help=tr("只预览，不写盘", "preview only"))
    apply.add_argument("--yes", action="store_true", help=tr("确认写盘", "confirm writes"))
    apply.add_argument("--force", action="store_true",
                       help=tr("忽略 Unity 运行与内容变化（不推荐）",
                               "ignore Unity lock / content drift (not recommended)"))

    rollback = sub.add_parser("rollback", help=tr("按日志回滚", "roll back from a journal"))
    rollback.add_argument("target", nargs="?", default=".", help=tr("工程目录", "project folder"))
    rollback.add_argument("--journal", help=tr("指定日志文件", "journal file"))
    rollback.add_argument("--last", action="store_true",
                          help=tr("使用最近一份日志", "use the latest journal"))
    rollback.add_argument("--dry-run", action="store_true")
    rollback.add_argument("--force", action="store_true")

    lic = sub.add_parser("license", help=tr("查看 / 激活授权", "show or activate a licence"))
    lic.add_argument("--show", action="store_true")
    lic.add_argument("--activate", metavar="KEY")
    lic.add_argument("--issue", nargs="?", const="", metavar="HOLDER",
                     help=tr("开发用：签发授权码", "developer: issue a key"))
    return parser


def _banner() -> str:
    if get_language() == "en":
        return f"""
  UnityGuard {__version__}
  Unity asset audit and safe cleanup - free edition, all features

  Double-clicking does nothing on purpose. Usage:

  Audit a project (read-only):
      drag the project folder onto UnityGuard.exe

  Or from this window:
      UnityGuard.exe "D:\\MyGame"                       audit
      UnityGuard.exe plan "D:\\MyGame" --rule fix        importer fixes
      UnityGuard.exe plan "D:\\MyGame" --rule dedupe     quarantine duplicates
      UnityGuard.exe apply --plan plan.json --dry-run    preview
      UnityGuard.exe apply --plan plan.json --yes        apply
      UnityGuard.exe rollback "D:\\MyGame" --last        roll back

  Plans and journals live in the project's .unityguard/ folder.
"""
    return f"""
  UnityGuard {__version__}
  Unity 素材体检与安全整理 · 免费完整版

  双击打开时什么都不会做。用法：

  体检一个工程（只读，免费）：
      把工程文件夹直接拖到 UnityGuard.exe 上

  或者在本窗口里：
      UnityGuard.exe "D:\\MyGame"                       体检
      UnityGuard.exe plan "D:\\MyGame" --rule fix        修导入设置
      UnityGuard.exe plan "D:\\MyGame" --rule dedupe     去重隔离
      UnityGuard.exe apply --plan 计划.json --dry-run    预览
      UnityGuard.exe apply --plan 计划.json --yes        执行
      UnityGuard.exe rollback "D:\\MyGame" --last        回滚

  计划和回滚日志都在工程的 .unityguard/ 目录里（Unity 不会导入）。
"""


def _wait_for_enter() -> None:
    try:
        input(tr("\n按回车键退出…", "\nPress Enter to exit..."))
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    raw = list(sys.argv[1:] if argv is None else argv)
    # --lang must win before any message is rendered, including argparse help.
    for index, token in enumerate(raw):
        if token == "--lang" and index + 1 < len(raw):
            try:
                set_language(raw[index + 1])
            except ValueError:
                pass
            break
        if token.startswith("--lang="):
            try:
                set_language(token.split("=", 1)[1])
            except ValueError:
                pass
            break

    if not raw:
        # Double-clicked: print guidance instead of silently auditing the exe folder.
        print(_banner())
        _wait_for_enter()
        return 0

    # Backwards compatible: `unity_asset_audit.py <project>` means audit.
    if raw[0] not in COMMANDS and not raw[0].startswith("-"):
        raw = ["audit"] + raw

    args = build_parser().parse_args(raw)
    handlers = {
        "audit": _cmd_audit,
        "plan": _cmd_plan,
        "apply": _cmd_apply,
        "rollback": _cmd_rollback,
        "license": _cmd_license,
    }
    handler = handlers.get(args.command or "audit")
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
