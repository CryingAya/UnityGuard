"""Render the audit as Markdown / JSON / self-contained HTML."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .i18n import tr
from .i18n_issues import localize
from .model import Asset, Issue, severity_label
from .project import ProjectInfo

_DISPLAY_CAP = 40

_CATEGORY_LABEL = {
    "texture": "贴图 / 2D",
    "engine": "引擎资产",
    "script": "脚本",
    "audio": "音频",
    "model": "模型",
    "shader": "着色器",
    "data": "数据文件",
    "font": "字体",
    "video": "视频",
    "other": "其他",
}


def _cat(key: str) -> str:
    return _CATEGORY_LABEL.get(key, key)


def health_score(issues: list[Issue]) -> int:
    critical = sum(1 for issue in issues if issue.severity == "critical")
    warn = sum(1 for issue in issues if issue.severity == "warn")
    info = sum(1 for issue in issues if issue.severity == "info")
    penalty = min(60, critical * 12) + min(25, warn * 3) + min(8, int(info * 0.5))
    return max(0, 100 - penalty)


def summarize(assets: list[Asset], issues: list[Issue]) -> dict:
    by_category: dict[str, dict[str, int]] = {}
    for asset in assets:
        slot = by_category.setdefault(asset.category, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += asset.size
    largest = sorted(assets, key=lambda asset: -asset.size)[:15]
    counts = {"critical": 0, "warn": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return {
        "assets": len(assets),
        "total_bytes": sum(asset.size for asset in assets),
        "by_category": dict(sorted(by_category.items(), key=lambda item: -item[1]["count"])),
        "largest": [
            {"path": asset.rel, "bytes": asset.size, "category": asset.category}
            for asset in largest
        ],
        "issue_counts": counts,
        "score": health_score(issues),
    }


def build_result(info: ProjectInfo, assets: list[Asset], issues: list[Issue],
                 extra: dict | None = None) -> dict:
    stats = summarize(assets, issues)
    return {
        "schema": "unityguard.audit/1",
        "tool_version": __version__,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "mode": "read-only",
        "project": {
            "root": str(info.root),
            "is_unity": info.is_unity,
            "unity_version": info.unity_version,
            "unity_locked": info.unity_locked,
            "has_addressables": info.has_addressables,
            "meta_ignored_by_git": info.has_meta_ignore,
            "notes": info.notes,
        },
        "summary": stats,
        "issues": [
            {
                "severity": issue.severity,
                "code": issue.code,
                "title": issue.title,
                "detail": localize(issue.detail),
                "hint": localize(issue.hint),
                "paths": issue.paths,
            }
            for issue in issues
        ],
        "extra": extra or {},
    }


def write_json(result: dict, path: Path) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _md_paths(issue: Issue) -> list[str]:
    shown = issue.paths[:_DISPLAY_CAP]
    lines = [f"- `{path}`" for path in shown]
    if len(issue.paths) > _DISPLAY_CAP:
        lines.append(tr(f"- …（共 {len(issue.paths)} 条，完整列表见 report.json）", f"- …({len(issue.paths)} total, see report.json)"))
    return lines


def write_markdown(result: dict, path: Path) -> None:
    project = result["project"]
    summary = result["summary"]
    counts = summary["issue_counts"]
    out: list[str] = []
    add = out.append

    add(tr("# UnityGuard 素材体检报告", "# UnityGuard audit report"))
    add("")
    add(tr(f"> 工程：`{project['root']}`  ", f"> Project: `{project['root']}`  "))
    add(tr(f"> 生成时间：{result['generated_at']}　模式：**只读**（未修改任何文件）  ",
           f"> Generated: {result['generated_at']} · mode: **read-only** (nothing modified)  "))
    if project["unity_version"]:
        add(tr(f"> Unity 版本：`{project['unity_version']}`  ", f"> Unity version: `{project['unity_version']}`  "))
    add("")
    add(tr(f"## 健康分：**{summary['score']} / 100**", f"## Health score: **{summary['score']} / 100**"))
    add("")
    add(tr("| 指标 | 数值 |", "| Metric | Value |"))
    add("| --- | --- |")
    add(tr(f"| 资源总数 | {summary['assets']} |", f"| Assets | {summary['assets']} |"))
    add(tr(f"| 总体积 | {_human(summary['total_bytes'])} |", f"| Total size | {_human(summary['total_bytes'])} |"))
    add(tr(f"| 严重 | **{counts.get('critical', 0)}** |", f"| Critical | **{counts.get('critical', 0)}** |"))
    add(tr(f"| 警告 | {counts.get('warn', 0)} |", f"| Warning | {counts.get('warn', 0)} |"))
    add(tr(f"| 提示 | {counts.get('info', 0)} |", f"| Notes | {counts.get('info', 0)} |"))
    add("")

    if project["notes"]:
        add(tr("### 环境提示", "### Environment notes"))
        add("")
        for note in project["notes"]:
            add(f"- {note}")
        add("")

    add(tr("## 资源分布", "## Assets by type"))
    add("")
    add(tr("| 类别 | 数量 | 体积 |", "| Type | Count | Size |"))
    add("| --- | --- | --- |")
    for category, stat in summary["by_category"].items():
        add(f"| {_cat(category)} | {stat['count']} | {_human(stat['bytes'])} |")
    add("")

    for severity in ("critical", "warn", "info"):
        bucket = [issue for issue in result["issues"] if issue["severity"] == severity]
        if not bucket:
            continue
        add(tr(f"## {severity_label(severity)}（{len(bucket)}）", f"## {severity_label(severity)} ({len(bucket)})"))
        add("")
        for index, issue in enumerate(bucket, 1):
            add(f"### {index}. {issue['title']}")
            add("")
            add(f"`{issue['code']}`")
            add("")
            if issue["detail"]:
                add(issue["detail"])
                add("")
            if issue["hint"]:
                add(tr(f"**建议：**{issue['hint']}", f"**Suggestion:** {issue['hint']}"))
                add("")
            if issue["paths"]:
                out.extend(_md_paths(_typed_issue(issue)))
                add("")

    add(tr("## 最大的资源（Top 15）", "## Largest assets (top 15)"))
    add("")
    add(tr("| 文件 | 体积 | 类别 |", "| File | Size | Type |"))
    add("| --- | --- | --- |")
    for item in summary["largest"]:
        add(f"| `{item['path']}` | {_human(item['bytes'])} | {_cat(item['category'])} |")
    add("")
    add("---")
    add("")
    add(tr("本次体检为**只读**：没有重命名、移动、覆盖或删除任何文件。", "This audit is **read-only**: nothing was renamed, moved, overwritten or deleted."))
    add(tr("报告由 UnityGuard 生成，可直接把 `report.json` 交给 `apply` 引擎做安全执行。", "Generated by UnityGuard. Pass `report.json` to the `apply` engine to act on it safely."))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _typed_issue(payload: dict) -> Issue:
    return Issue(
        severity=payload.get("severity", "info"),
        code=payload.get("code", ""),
        title=payload.get("title", ""),
        detail=payload.get("detail", ""),
        hint=payload.get("hint", ""),
        paths=list(payload.get("paths", [])),
    )


_CSS = """
:root { color-scheme: dark; }
body { margin:0; padding:32px; background:#0f1115; color:#e6e8ee;
       font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
h1,h2,h3 { font-weight:650; letter-spacing:.2px; }
h1 { font-size:26px; margin:0 0 8px; }
.meta { color:#8b93a7; font-size:13px; margin-bottom:24px; }
.score { display:flex; gap:16px; align-items:baseline; margin:20px 0 28px; }
.score .num { font-size:52px; font-weight:700; }
.pill { display:inline-block; padding:1px 9px; border-radius:999px; font-size:12px;
        font-weight:600; margin-right:6px; }
.critical { background:#3b1418; color:#ff8b8b; border:1px solid #7d2930; }
.warn     { background:#3a2b12; color:#ffcc7a; border:1px solid #7a5a1d; }
.info     { background:#152a3b; color:#8fd0ff; border:1px solid #29546f; }
table { border-collapse:collapse; width:100%; margin:12px 0 28px; font-size:13px; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid #232733; }
th { color:#8b93a7; font-weight:600; }
code { background:#191d26; padding:1px 5px; border-radius:4px; font-size:12.5px; }
details { border:1px solid #232733; border-radius:10px; margin:10px 0; background:#141821; }
summary { cursor:pointer; padding:12px 14px; font-weight:600; }
summary::marker { color:#5d6579; }
.body { padding:0 14px 14px; border-top:1px solid #232733; }
.body p { margin:12px 0; color:#b9bfd0; }
.paths { max-height:240px; overflow:auto; background:#0d1017; border-radius:8px; padding:10px; }
.paths div { font-size:12px; color:#9aa3b8; font-family:ui-monospace,Consolas,monospace; }
.hint { color:#9fe0b5; }
.empty { color:#6f7789; }
"""


def write_html(result: dict, path: Path) -> None:
    project = result["project"]
    summary = result["summary"]
    counts = summary["issue_counts"]
    esc = html.escape
    parts: list[str] = []
    parts.append("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>")
    parts.append(f"<title>UnityGuard 体检报告 · {esc(Path(project['root']).name)}</title>")
    parts.append(f"<style>{_CSS}</style><body>")
    parts.append(tr("<h1>UnityGuard 素材体检报告</h1>", "<h1>UnityGuard audit report</h1>"))
    parts.append(tr(
        f"<div class='meta'>{esc(project['root'])} · {esc(result['generated_at'])} · 模式：只读",
        f"<div class='meta'>{esc(project['root'])} · {esc(result['generated_at'])} · mode: read-only",
    )
        + (f" · Unity {esc(project['unity_version'])}" if project["unity_version"] else "")
        + "</div>"
    )
    parts.append(
        "<div class='score'><span class='num'>"
        f"{summary['score']}</span><span>/ 100</span>"
    )
    parts.append(tr("<span class='pill critical'>严重 ", "<span class='pill critical'>Critical ") + str(counts.get("critical", 0)) + "</span>")
    parts.append(tr("<span class='pill warn'>警告 ", "<span class='pill warn'>Warnings ") + str(counts.get("warn", 0)) + "</span>")
    parts.append(tr("<span class='pill info'>提示 ", "<span class='pill info'>Notes ") + str(counts.get("info", 0)) + "</span></div>")

    parts.append(tr("<table><tr><th>指标</th><th>数值</th></tr>", "<table><tr><th>Metric</th><th>Value</th></tr>"))
    parts.append(tr(f"<tr><td>资源总数</td><td>{summary['assets']}</td></tr>", f"<tr><td>Assets</td><td>{summary['assets']}</td></tr>"))
    parts.append(tr(f"<tr><td>总体积</td><td>{_human(summary['total_bytes'])}</td></tr>", f"<tr><td>Total size</td><td>{_human(summary['total_bytes'])}</td></tr>"))
    for category, stat in summary["by_category"].items():
        parts.append(tr(
            f"<tr><td>{esc(_cat(category))}</td><td>{stat['count']} 个 · "
            f"{_human(stat['bytes'])}</td></tr>",
            f"<tr><td>{esc(_cat(category))}</td><td>{stat['count']} · "
            f"{_human(stat['bytes'])}</td></tr>",
        ))
    parts.append("</table>")

    for severity in ("critical", "warn", "info"):
        bucket = [issue for issue in result["issues"] if issue["severity"] == severity]
        if not bucket:
            continue
        parts.append(tr(f"<h2>{severity_label(severity)}（{len(bucket)}）</h2>", f"<h2>{severity_label(severity)} ({len(bucket)})</h2>"))
        for issue in bucket:
            parts.append("<details><summary>")
            parts.append(f"<span class='pill {severity}'>{severity_label(severity)}</span>")
            parts.append(esc(issue["title"]))
            parts.append(f" <code>{esc(issue['code'])}</code></summary><div class='body'>")
            if issue["detail"]:
                parts.append(f"<p>{esc(issue['detail'])}</p>")
            if issue["hint"]:
                parts.append(tr(f"<p class='hint'>建议：{esc(issue['hint'])}</p>", f"<p class='hint'>Suggestion: {esc(issue['hint'])}</p>"))
            if issue["paths"]:
                parts.append("<div class='paths'>")
                for item in issue["paths"][:_DISPLAY_CAP]:
                    parts.append(f"<div>{esc(item)}</div>")
                if len(issue["paths"]) > _DISPLAY_CAP:
                    parts.append(
                        tr(f"<div>…（共 {len(issue['paths'])} 条，完整列表见 report.json）</div>", f"<div>…({len(issue['paths'])} total, see report.json)</div>")
                    )
                parts.append("</div>")
            parts.append("</div></details>")
    if not result["issues"]:
        parts.append(tr("<p class='empty'>没有发现问题。这个工程相当干净。</p>", "<p class='empty'>No issues found. This project is clean.</p>"))

    parts.append("</body></html>")
    path.write_text("".join(parts), encoding="utf-8")
