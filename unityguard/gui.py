"""A small Tkinter front-end for people who do not want a terminal.

Design goals, in order: nothing destructive without an explicit click, every
long operation off the UI thread, and the same engine calls the CLI uses - the
GUI is a view, not a second implementation.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .apply import apply_plan, rollback_journal
from .cli import run_audit
from .dedupe import build_dedupe_plan
from .i18n import get_language, set_language, tr
from .licensing import FREE_EDITION
from .metaedit import build_fix_plan
from .plan import build_category_plan, save_plan
from .project import locate
from .scan import scan

_SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}
_SEVERITY_TEXT = {"critical": "critical", "warn": "warn", "info": "info"}

_TEXT = {
    "title": ("UnityGuard · Unity 素材体检与安全整理", "UnityGuard · Unity asset audit"),
    "project": ("工程", "Project"),
    "browse": ("选择工程…", "Choose…"),
    "audit": ("体检", "Audit"),
    "fix": ("修复导入设置", "Fix importers"),
    "category": ("按类型整理", "Sort by type"),
    "dedupe": ("去重隔离", "Quarantine duplicates"),
    "apply": ("执行计划", "Apply plan"),
    "rollback": ("回滚", "Roll back"),
    "tab_report": ("体检报告", "Report"),
    "tab_plan": ("变更计划", "Plan"),
    "tab_log": ("日志", "Log"),
    "col_sev": ("级别", "Level"),
    "col_title": ("问题", "Issue"),
    "col_code": ("代码", "Code"),
    "col_kind": ("类型", "Kind"),
    "col_from": ("原路径", "From"),
    "col_to": ("新路径", "To"),
    "hint_select": ("先选择一个 Unity 工程文件夹。", "Pick a Unity project folder first."),
    "ready": ("就绪", "Ready"),
    "no_project": ("还没有选择工程", "no project selected"),
    "scanning": ("正在体检…", "Auditing…"),
    "planning": ("正在生成计划…", "Building plan…"),
    "applying": ("正在执行…", "Applying…"),
    "rolling": ("正在回滚…", "Rolling back…"),
    "done": ("完成", "Done"),
    "failed": ("失败", "Failed"),
    "edition_free": ("免费完整版", "Free edition"),
    "need_plan": ("先生成一份计划。", "Build a plan first."),
    "confirm_apply": (
        "即将对工程写入 {n} 个变更：\n\n{root}\n\n会自动写入回滚日志，随时可撤销。确定执行？",
        "About to apply {n} changes to:\n\n{root}\n\nA rollback journal will be written. Continue?",
    ),
    "confirm_rollback": (
        "回滚最近一次变更？\n\n{journal}",
        "Roll back the most recent change?\n\n{journal}",
    ),
    "no_journal": ("这个工程还没有可回滚的记录。", "No rollback journal in this project yet."),
    "plan_saved": ("计划已保存：{path}", "Plan saved: {path}"),
    "applied": ("执行成功：{n} 个变更", "Applied {n} changes"),
    "rolled": ("已回滚：{n} 个变更", "Rolled back {n} changes"),
    "blocked": ("被安全检查拦下：\n\n{why}", "Blocked by a safety check:\n\n{why}"),
    "empty_plan": ("没有需要处理的内容。", "Nothing to do."),
    "lang": ("语言", "Language"),
    "note_readonly": (
        "体检只读，不改任何文件；执行前会先让你确认。",
        "The audit only reads. You confirm before anything is written.",
    ),
}


def t(key: str, **params) -> str:
    zh, en = _TEXT[key]
    text = en if get_language() == "en" else zh
    return text.format(**params) if params else text


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.project: Path | None = None
        self.info = None
        self.state = None
        self.plan = None
        self.plan_path: Path | None = None
        self.journal: Path | None = None
        self._busy = False
        self._queue: queue.Queue = queue.Queue()
        self._poll_id = None

        self.title(f"UnityGuard {__version__}")
        # Tk geometry is expressed in scaled units while the screen size is in
        # physical pixels; divide by the tk scaling factor or the window spills
        # off the right edge on a 125%/150% display.
        try:
            scaling = float(self.tk.call("tk", "scaling")) or 1.0
        except (tk.TclError, ValueError):
            scaling = 1.0
        screen_w = self.winfo_screenwidth() / scaling
        screen_h = self.winfo_screenheight() / scaling
        width = int(min(1000, max(880, screen_w - 140)))
        height = int(min(680, max(540, screen_h - 140)))
        self.geometry(f"{width}x{height}+{int(max(10, (screen_w - width) / 2))}"
                      f"+{int(max(10, (screen_h - height) / 3))}")
        self.minsize(880, 540)
        self._style()
        self._build()
        self._retranslate()
        self.log(tr("就绪。先选择工程文件夹。", "Ready. Choose a project folder."))

    # ------------------------------------------------------------------ ui
    def _style(self) -> None:
        try:
            self.option_add("*Font", ("Microsoft YaHei UI", 10))
        except tk.TclError:
            pass
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Head.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Muted.TLabel", foreground="#666")
        style.configure("Danger.TLabel", foreground="#b23")
        style.configure("Ok.TLabel", foreground="#1a7f37")

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(14, 12, 14, 6))
        top.pack(fill="x")

        self.lbl_project = ttk.Label(top, text=t("project"), style="Head.TLabel")
        self.lbl_project.grid(row=0, column=0, sticky="w")
        self.var_path = tk.StringVar(value="")
        entry = ttk.Entry(top, textvariable=self.var_path, state="readonly")
        entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.btn_browse = ttk.Button(top, text=t("browse"), command=self.choose_project)
        self.btn_browse.grid(row=0, column=2)

        self.lbl_lang = ttk.Label(top, text=t("lang"))
        self.lbl_lang.grid(row=0, column=3, padx=(16, 4))
        self.var_lang = tk.StringVar(value="中文" if get_language() == "zh" else "English")
        self.cmb_lang = ttk.Combobox(top, textvariable=self.var_lang, width=8,
                                     values=("中文", "English"), state="readonly")
        self.cmb_lang.grid(row=0, column=4)
        self.cmb_lang.bind("<<ComboboxSelected>>", self._on_language)

        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(14, 4, 14, 8))
        bar.pack(fill="x")
        self.btn_audit = ttk.Button(bar, text=t("audit"), command=self.do_audit)
        self.btn_fix = ttk.Button(bar, text=t("fix"), command=lambda: self.do_plan("fix"))
        self.btn_category = ttk.Button(bar, text=t("category"),
                                       command=lambda: self.do_plan("category"))
        self.btn_dedupe = ttk.Button(bar, text=t("dedupe"), command=lambda: self.do_plan("dedupe"))
        for index, button in enumerate((self.btn_audit, self.btn_fix, self.btn_category,
                                        self.btn_dedupe)):
            button.grid(row=0, column=index, padx=(0, 6))
        self.btn_apply = ttk.Button(bar, text=t("apply"), command=self.do_apply, state="disabled")
        self.btn_apply.grid(row=0, column=6, padx=(24, 6))
        self.btn_rollback = ttk.Button(bar, text=t("rollback"), command=self.do_rollback)
        self.btn_rollback.grid(row=0, column=7)
        bar.columnconfigure(5, weight=1)

        # 先给底部状态栏占位，再让 Notebook 吃掉剩余空间
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        self.status = ttk.Label(self, text=t("ready"), padding=(16, 7), style="Muted.TLabel")
        self.status.pack(side="bottom", fill="x")

        self.nb = ttk.Notebook(self, padding=(14, 6, 14, 0))
        self.nb.pack(fill="both", expand=True)

        # report tab: draggable split so the table never gets squeezed
        report_tab = ttk.Frame(self.nb)
        self.nb.add(report_tab, text=t("tab_report"))
        paned = ttk.PanedWindow(report_tab, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        paned.add(left, weight=3)
        self.tree = ttk.Treeview(left, columns=("sev", "title", "code"),
                                 show="headings", selectmode="browse")
        self.tree.heading("sev", text=t("col_sev"))
        self.tree.heading("title", text=t("col_title"))
        self.tree.heading("code", text=t("col_code"))
        self.tree.column("sev", width=64, anchor="center", stretch=False)
        self.tree.column("title", width=320, stretch=True)
        self.tree.column("code", width=176, stretch=True)
        self.tree.tag_configure("critical", foreground="#b23")
        self.tree.tag_configure("warn", foreground="#a2670b")
        self.tree.tag_configure("info", foreground="#2a5d9f")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_issue)
        yscroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        self.detail = tk.Text(right, width=34, height=12, wrap="word", padx=10, pady=8,
                              relief="flat", background="#fbfbfd")
        self.detail.pack(fill="both", expand=True)
        self._reset_detail()

        # plan tab
        plan_tab = ttk.Frame(self.nb)
        self.nb.add(plan_tab, text=t("tab_plan"))
        self.plan_tree = ttk.Treeview(plan_tab, columns=("kind", "from", "to"),
                                      show="headings", selectmode="browse")
        self.plan_tree.heading("kind", text=t("col_kind"))
        self.plan_tree.heading("from", text=t("col_from"))
        self.plan_tree.heading("to", text=t("col_to"))
        self.plan_tree.column("kind", width=86, anchor="center", stretch=False)
        self.plan_tree.column("from", width=430, stretch=True)
        self.plan_tree.column("to", width=430, stretch=True)
        self.plan_tree.grid(row=0, column=0, sticky="nsew")
        plan_scroll = ttk.Scrollbar(plan_tab, orient="vertical", command=self.plan_tree.yview)
        self.plan_tree.configure(yscrollcommand=plan_scroll.set)
        plan_scroll.grid(row=0, column=1, sticky="ns")
        plan_tab.columnconfigure(0, weight=1)
        plan_tab.rowconfigure(0, weight=1)

        # log tab
        log_tab = ttk.Frame(self.nb)
        self.nb.add(log_tab, text=t("tab_log"))
        self.log_text = tk.Text(log_tab, height=8, wrap="word", padx=10, pady=8, relief="flat",
                                background="#101216", foreground="#d6dae3",
                                insertbackground="#d6dae3")
        self.log_text.pack(fill="both", expand=True)



    def _retranslate(self) -> None:
        self.lbl_project.configure(text=t("project") + "：")
        self.btn_browse.configure(text=t("browse"))
        self.lbl_lang.configure(text=t("lang") + "：")
        self.btn_audit.configure(text=t("audit"))
        self.btn_fix.configure(text=t("fix"))
        self.btn_category.configure(text=t("category"))
        self.btn_dedupe.configure(text=t("dedupe"))
        self.btn_apply.configure(text=t("apply"))
        self.btn_rollback.configure(text=t("rollback"))
        self.nb.tab(0, text=t("tab_report"))
        self.nb.tab(1, text=t("tab_plan"))
        self.nb.tab(2, text=t("tab_log"))
        self.tree.heading("sev", text=t("col_sev"))
        self.tree.heading("title", text=t("col_title"))
        self.tree.heading("code", text=t("col_code"))
        self.plan_tree.heading("kind", text=t("col_kind"))
        self.plan_tree.heading("from", text=t("col_from"))
        self.plan_tree.heading("to", text=t("col_to"))
        self.status.configure(text=t("ready") if not self.project else
                              f"{t('edition_free')} · {t('note_readonly')}")

    # -------------------------------------------------------------- helpers
    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _bg(self, work, done) -> None:
        """Run `work()` off the UI thread, then `done(result)` on it.

        Tkinter is not thread-safe, so the worker only puts its result on a
        queue; the main thread polls that queue - `after()` called from another
        thread silently does nothing on some Tk builds.
        """
        if self._busy:
            return
        self._busy = True
        self._set_buttons("disabled")

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - surfaced in the UI
                result = exc
            self._queue.put((done, result))

        threading.Thread(target=runner, daemon=True).start()
        self._poll_id = self.after(60, self._poll)

    def _poll(self) -> None:
        self._poll_id = None
        while True:
            try:
                done, result = self._queue.get_nowait()
            except queue.Empty:
                break
            self._finish(done, result)
        if self._busy:
            self._poll_id = self.after(60, self._poll)

    def _finish(self, done, result) -> None:
        self._busy = False
        self._set_buttons("normal")
        if isinstance(result, Exception):
            self.log(f"[{t('failed')}] {result}")
            messagebox.showerror(t("failed"), str(result))
            return
        done(result)

    def _set_buttons(self, state: str) -> None:
        for button in (self.btn_audit, self.btn_fix, self.btn_category,
                       self.btn_dedupe, self.btn_rollback, self.btn_browse):
            button.configure(state=state)
        if state == "normal":
            self.btn_apply.configure(state="normal" if self.plan else "disabled")

    # --------------------------------------------------------------- events
    def _on_language(self, _event=None) -> None:
        set_language("zh" if self.var_lang.get() == "中文" else "en")
        self._retranslate()
        self._reset_detail()
        if self.project:
            # Findings are localized when the audit runs, so re-run it.
            self.do_audit()

    def _reset_detail(self) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", tr("选中左侧的问题查看详情。",
                                     "Select an issue on the left to see details."))
        self.detail.configure(state="disabled")

    def _on_select_issue(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        payload = getattr(self, "_issue_details", {}).get(selection[0])
        if not payload:
            return
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", payload)
        self.detail.configure(state="disabled")

    def choose_project(self) -> None:
        folder = filedialog.askdirectory(title=t("project"))
        if not folder:
            return
        info = locate(Path(folder))
        if not info.is_unity:
            messagebox.showwarning(t("project"), tr(
                "这个文件夹看起来不是 Unity 工程（没有 Assets/ 和 ProjectSettings/）。",
                "This does not look like a Unity project (no Assets/ and ProjectSettings/)."))
            return
        self.project = info.root
        self.info = info
        self.var_path.set(str(info.root))
        self.plan = None
        self.plan_path = None
        self.btn_apply.configure(state="disabled")
        self.log(tr("已选择工程：{p}", "Project selected: {p}", p=info.root))

    # ---------------------------------------------------------------- audit
    def do_audit(self) -> None:
        if not self.project:
            messagebox.showinfo(t("project"), t("hint_select"))
            return
        self.status.configure(text=t("scanning"))
        self.log(t("scanning"))
        project = self.project

        def work():
            return run_audit(project)

        def done(result: dict) -> None:
            self._show_report(result)
            self.status.configure(text=tr(
                "健康分 {score} · 严重 {c} · 警告 {w} · 提示 {i}",
                "Score {score} · critical {c} · warnings {w} · notes {i}",
                score=result["summary"]["score"],
                c=result["summary"]["issue_counts"].get("critical", 0),
                w=result["summary"]["issue_counts"].get("warn", 0),
                i=result["summary"]["issue_counts"].get("info", 0)))
            self.log(tr("体检完成：{n} 个问题", "Audit done: {n} findings",
                        n=len(result["issues"])))

        self._bg(work, done)

    def _show_report(self, result: dict) -> None:
        self.tree.delete(*self.tree.get_children())
        self._issue_details: dict[str, str] = {}
        issues = sorted(result["issues"],
                        key=lambda issue: _SEVERITY_ORDER.get(issue["severity"], 9))
        for issue in issues:
            label = tr({"critical": "严重", "warn": "警告", "info": "提示"}[issue["severity"]],
                       {"critical": "critical", "warn": "warn",
                        "info": "info"}[issue["severity"]])
            node = self.tree.insert("", "end",
                                    values=(label, issue["title"], issue["code"]),
                                    tags=(issue["severity"],))
            lines = [issue["title"], "", issue.get("detail", "")]
            if issue.get("hint"):
                lines += ["", tr("建议：", "Suggestion: ") + issue["hint"]]
            if issue.get("paths"):
                lines += ["", tr("涉及文件：", "Files:")] + issue["paths"][:40]
            self._issue_details[node] = "\n".join(line for line in lines if line is not None)
        self.nb.select(0)

    # ----------------------------------------------------------------- plan
    def do_plan(self, rule: str) -> None:
        if not self.project:
            messagebox.showinfo(t("project"), t("hint_select"))
            return
        self.status.configure(text=t("planning"))
        self.log(t("planning"))
        project = self.project

        def work():
            state = scan(project)
            if rule == "fix":
                built = build_fix_plan(state.info, state.assets, state.index)
            elif rule == "dedupe":
                built = build_dedupe_plan(state)
            else:
                built = build_category_plan(state.info, state.assets)
            path = save_plan(built, state.info.root / ".unityguard" / "plans"
                             / f"gui_{rule}.json")
            return built, path

        def done(result) -> None:
            plan, path = result
            self.plan = plan
            self.plan_path = path
            self._show_plan(plan)
            self.btn_apply.configure(state="normal" if plan.operations else "disabled")
            self.log(t("plan_saved", path=path))
            if not plan.operations:
                self.status.configure(text=t("empty_plan"))

        self._bg(work, done)

    def _show_plan(self, plan) -> None:
        self.plan_tree.delete(*self.plan_tree.get_children())
        for op in plan.operations:
            if op.is_temp:
                continue
            if op.kind == "meta_edit":
                target = ", ".join(f"{e['key']} {e['old']}→{e['new']}" for e in op.edits)
            else:
                target = op.to_rel
            self.plan_tree.insert("", "end", values=(op.kind, op.from_rel, target))
        self.nb.select(1)
        for warning in plan.warnings[:5]:
            self.log(f"! {warning}")
        for error in plan.errors:
            self.log(f"[{t('failed')}] {error}")

    # ---------------------------------------------------------------- apply
    def do_apply(self) -> None:
        if not self.project or not self.plan:
            messagebox.showinfo(t("apply"), t("need_plan"))
            return
        real = [op for op in self.plan.operations if not op.is_temp]
        if not messagebox.askyesno(t("apply"),
                                   t("confirm_apply", n=len(real), root=self.project)):
            return
        self.status.configure(text=t("applying"))
        self.log(t("applying"))
        plan = self.plan
        info = self.info

        def work():
            return apply_plan(info, plan, tier="free")

        def done(result) -> None:
            if not result.ok:
                self.log(f"[{t('blocked')}] " + "；".join(result.failed[:3]))
                messagebox.showwarning(t("failed"), t("blocked",
                                                      why="\n".join(result.failed[:5])))
                self.status.configure(text=t("failed"))
                return
            self.journal = Path(result.journal_path) if result.journal_path else None
            self.plan = None
            self.btn_apply.configure(state="disabled")
            self.log(t("applied", n=result.applied))
            for message in result.messages:
                self.log(f"  {message}")
            self.status.configure(text=t("done"))
            self.do_audit()

        self._bg(work, done)

    # ------------------------------------------------------------- rollback
    def do_rollback(self) -> None:
        if not self.project:
            messagebox.showinfo(t("project"), t("hint_select"))
            return
        journals = sorted((self.project / ".unityguard" / "journals").glob("rollback_*.json"))
        if not journals:
            messagebox.showinfo(t("rollback"), t("no_journal"))
            return
        journal = journals[-1]
        if not messagebox.askyesno(t("rollback"), t("confirm_rollback", journal=journal)):
            return
        self.status.configure(text=t("rolling"))
        self.log(t("rolling"))

        def work():
            return rollback_journal(journal)

        def done(result) -> None:
            if not result.ok:
                self.log(f"[{t('failed')}] " + "；".join(result.failed[:3]))
                messagebox.showwarning(t("failed"), "\n".join(result.failed[:5]))
                self.status.configure(text=t("failed"))
                return
            self.log(t("rolled", n=result.applied))
            self.status.configure(text=t("done"))
            self.do_audit()

        self._bg(work, done)


def main() -> int:
    try:
        # Crisp text on high-DPI screens (Windows only, best effort).
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass

    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
