"""CustomTkinter workbench GUI."""

from __future__ import annotations

from dataclasses import replace
import queue
import webbrowser
from pathlib import Path
from typing import Any


try:  # pragma: no cover - optional desktop dependency
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import filedialog, messagebox
except ImportError:  # pragma: no cover - exercised by smoke import test
    ctk = None  # type: ignore[assignment]
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]

from researchclaw.gui.tasks import TaskEvent, run_background
from researchclaw.workbench.controller import WorkbenchController
from researchclaw.workbench.remote import RemoteProfile


class WorkbenchApp:
    """Thin GUI shell over the workbench controller."""

    def __init__(self) -> None:
        if ctk is None:
            raise RuntimeError(
                "customtkinter is required for the GUI. Install it with "
                "`pip install customtkinter` or `pip install -e .[gui]`."
            )
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        self.controller = WorkbenchController()
        self.root = ctk.CTk()
        self.root.title("AutoPaperWorker Workbench")
        self.root.geometry("1240x820")
        self._events: "queue.Queue[TaskEvent]" = queue.Queue()
        self._task_running = False
        self._build_layout()
        self.root.after(100, self._drain_events)

    def mainloop(self) -> None:
        self.root.mainloop()

    def _build_layout(self) -> None:
        outer = ctk.CTkFrame(self.root)
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        header = ctk.CTkFrame(outer)
        header.pack(fill="x", padx=12, pady=(12, 8))
        ctk.CTkLabel(
            header,
            text="AutoPaperWorker Workbench",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(side="left", padx=12, pady=12)
        self.status_var = tk.StringVar(value="就绪") if tk else None
        self.status_label = ctk.CTkLabel(header, textvariable=self.status_var)
        self.status_label.pack(side="right", padx=12)

        self.progress = ctk.CTkProgressBar(header, width=220)
        self.progress.pack(side="right", padx=8, pady=12)
        self.progress.set(0)

        self.tabs = ctk.CTkTabview(outer)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for name in ("任务", "检索", "CNKI", "模型", "毕设", "远程", "日志"):
            self.tabs.add(name)

        self._build_task_tab(self.tabs.tab("任务"))
        self._build_search_tab(self.tabs.tab("检索"))
        self._build_cnki_tab(self.tabs.tab("CNKI"))
        self._build_model_tab(self.tabs.tab("模型"))
        self._build_project_tab(self.tabs.tab("毕设"))
        self._build_remote_tab(self.tabs.tab("远程"))
        self._build_log_tab(self.tabs.tab("日志"))

    def _build_task_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab, text="论文/研究题目").grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        self.topic_entry = ctk.CTkEntry(tab, placeholder_text="输入论文题目或毕设题目")
        self.topic_entry.grid(row=0, column=1, sticky="ew", padx=16, pady=(16, 4))

        ctk.CTkLabel(tab, text="实验模式").grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.experiment_mode = ctk.CTkComboBox(tab, values=["simulated", "sandbox", "docker", "ssh_remote"])
        self.experiment_mode.set("simulated")
        self.experiment_mode.grid(row=1, column=1, sticky="w", padx=16, pady=4)

        ctk.CTkLabel(tab, text="输出目录").grid(row=2, column=0, sticky="w", padx=16, pady=4)
        self.output_entry = ctk.CTkEntry(tab, placeholder_text="可选，默认 artifacts/")
        self.output_entry.grid(row=2, column=1, sticky="ew", padx=16, pady=4)

        btns = ctk.CTkFrame(tab)
        btns.grid(row=3, column=0, columnspan=2, sticky="w", padx=16, pady=12)
        ctk.CTkButton(btns, text="预检索", command=self._on_search).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="生成毕设计划", command=self._on_plan).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="分析项目", command=self._on_analyze_project).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="运行 Pipeline", command=self._on_run_pipeline).pack(side="left")

        self.task_output = ctk.CTkTextbox(tab)
        self.task_output.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=16, pady=(8, 16))
        tab.grid_rowconfigure(4, weight=1)

    def _build_search_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(tab)
        top.pack(fill="x", padx=16, pady=16)
        ctk.CTkLabel(top, text="检索关键词").pack(side="left", padx=(12, 8), pady=12)
        self.search_entry = ctk.CTkEntry(top, placeholder_text="例如：graph neural networks")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=12)
        self.search_limit = ctk.CTkEntry(top, width=80)
        self.search_limit.insert(0, "10")
        self.search_limit.pack(side="left", padx=(0, 8), pady=12)
        search_btn = ctk.CTkButton(top, text="搜索", command=self._on_search)
        search_btn.pack(side="left", padx=(0, 8), pady=12)
        ctk.CTkButton(top, text="打开 CNKI", command=self._open_cnki).pack(side="left", pady=12)

        self.search_output = ctk.CTkTextbox(tab)
        self.search_output.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _build_cnki_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="CNKI 检索词").grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        self.cnki_topic_entry = ctk.CTkEntry(tab, placeholder_text="输入中文关键词或题目")
        self.cnki_topic_entry.grid(row=0, column=1, sticky="ew", padx=16, pady=(16, 4))

        ctk.CTkLabel(tab, text="导入文件").grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.cnki_paths_entry = ctk.CTkEntry(tab, placeholder_text="RIS / BibTeX / TXT / PDF 路径")
        self.cnki_paths_entry.grid(row=1, column=1, sticky="ew", padx=16, pady=4)

        btns = ctk.CTkFrame(tab)
        btns.grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=12)
        ctk.CTkButton(btns, text="打开知网", command=self._open_cnki).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="选择文件", command=self._browse_cnki_files).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="导入资料", command=self._on_import_cnki).pack(side="left")

        self.cnki_output = ctk.CTkTextbox(tab)
        self.cnki_output.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=16, pady=(8, 16))
        tab.grid_rowconfigure(3, weight=1)

    def _build_model_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(1, weight=1)
        self.model_mode = ctk.CTkComboBox(tab, values=["cloud", "local"])
        self.model_mode.set("cloud")
        self.model_provider = ctk.CTkComboBox(tab, values=["openai", "openrouter", "deepseek", "anthropic"])
        self.model_provider.set("openai")
        self.model_base_url = ctk.CTkEntry(tab, placeholder_text="https://api.openai.com/v1")
        self.model_model = ctk.CTkEntry(tab, placeholder_text="gpt-4o-mini")
        self.model_key_env = ctk.CTkEntry(tab, placeholder_text="OPENAI_API_KEY")

        rows = [
            ("模型模式", self.model_mode),
            ("云端 provider", self.model_provider),
            ("Base URL", self.model_base_url),
            ("模型名", self.model_model),
            ("API key env", self.model_key_env),
        ]
        for idx, (label, widget) in enumerate(rows):
            ctk.CTkLabel(tab, text=label).grid(row=idx, column=0, sticky="w", padx=16, pady=6)
            widget.grid(row=idx, column=1, sticky="ew", padx=16, pady=6)

        ctk.CTkButton(tab, text="生成模型配置摘要", command=self._on_render_model_config).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", padx=16, pady=12
        )
        self.model_output = ctk.CTkTextbox(tab, height=180)
        self.model_output.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="nsew", padx=16, pady=(8, 16))
        tab.grid_rowconfigure(len(rows) + 1, weight=1)

    def _build_project_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="项目路径").grid(row=0, column=0, sticky="w", padx=16, pady=(16, 4))
        self.project_path_entry = ctk.CTkEntry(tab, placeholder_text="选择已有代码项目目录")
        self.project_path_entry.grid(row=0, column=1, sticky="ew", padx=16, pady=(16, 4))

        btns = ctk.CTkFrame(tab)
        btns.grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=12)
        ctk.CTkButton(btns, text="选择目录", command=self._browse_project_dir).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="分析项目", command=self._on_analyze_project).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="生成计划", command=self._on_plan).pack(side="left")

        self.project_output = ctk.CTkTextbox(tab)
        self.project_output.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=16, pady=(8, 16))
        tab.grid_rowconfigure(2, weight=1)

    def _build_remote_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(1, weight=1)
        self.remote_platform = ctk.CTkComboBox(tab, values=["autodl", "gpuhome", "custom"])
        self.remote_platform.set("autodl")
        self.remote_ssh = ctk.CTkEntry(tab, placeholder_text="ssh root@host -p 22")
        self.remote_host = ctk.CTkEntry(tab, placeholder_text="host")
        self.remote_user = ctk.CTkEntry(tab, placeholder_text="user")
        self.remote_port = ctk.CTkEntry(tab, placeholder_text="22")
        self.remote_key = ctk.CTkEntry(tab, placeholder_text="SSH key path")
        self.remote_password = ctk.CTkEntry(tab, placeholder_text="Password", show="*")
        # Placeholder for remote Linux workdir.
        self.remote_workdir = ctk.CTkEntry(tab, placeholder_text="/tmp/researchclaw_experiments")  # nosec B108
        self.remote_local = ctk.CTkEntry(tab, placeholder_text="本地项目目录")
        self.remote_command = ctk.CTkEntry(tab, placeholder_text="python train.py")

        rows = [
            ("平台", self.remote_platform),
            ("SSH 指令", self.remote_ssh),
            ("Host", self.remote_host),
            ("User", self.remote_user),
            ("Port", self.remote_port),
            ("Key path", self.remote_key),
            ("Password", self.remote_password),
            ("远程目录", self.remote_workdir),
            ("本地目录", self.remote_local),
            ("远程命令", self.remote_command),
        ]
        for idx, (label, widget) in enumerate(rows):
            ctk.CTkLabel(tab, text=label).grid(row=idx, column=0, sticky="w", padx=16, pady=6)
            widget.grid(row=idx, column=1, sticky="ew", padx=16, pady=6)

        btns = ctk.CTkFrame(tab)
        btns.grid(row=len(rows), column=0, columnspan=2, sticky="w", padx=16, pady=12)
        ctk.CTkButton(btns, text="测试连接", command=self._on_remote_test).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="上传代码", command=self._on_remote_upload).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="运行命令", command=self._on_remote_run).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="下载结果", command=self._on_remote_download).pack(side="left")

        self.remote_output = ctk.CTkTextbox(tab, height=180)
        self.remote_output.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="nsew", padx=16, pady=(8, 16))
        tab.grid_rowconfigure(len(rows) + 1, weight=1)

    def _build_log_tab(self, tab: Any) -> None:
        tab.grid_columnconfigure(0, weight=1)
        self.log_output = ctk.CTkTextbox(tab)
        self.log_output.pack(fill="both", expand=True, padx=16, pady=16)
        self._append_log("Workbench ready.")

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        if self.status_var is not None:
            self.status_var.set(message or ("运行中" if busy else "就绪"))
        self.progress.set(0.45 if busy else 0.0)
        self._task_running = busy

    def _append_log(self, text: str) -> None:
        self.log_output.insert("end", text.rstrip() + "\n")
        self.log_output.see("end")

    def _append_textbox(self, widget: Any, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("end", text.rstrip() + "\n")
        widget.see("end")

    def _queue_log(self, text: str) -> None:
        self._events.put(TaskEvent("log", text))

    def _start_task(self, label: str, func) -> None:
        if self._task_running:
            self._queue_log("已有任务在运行，请稍后。")
            return
        self._set_busy(True, label)

        def _run() -> object:
            return func()

        run_background(_run, self._events)

    def _drain_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                if event.kind == "log":
                    self._append_log(str(event.payload))
                elif event.kind == "result":
                    self._handle_task_result(event.payload)
                elif event.kind == "error":
                    self._handle_task_error(str(event.payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._drain_events)

    def _handle_task_result(self, payload: object) -> None:
        self._set_busy(False)
        if isinstance(payload, tuple) and len(payload) == 2:
            target, text = payload
            if target == "search":
                self._append_textbox(self.search_output, str(text))
            elif target == "cnki":
                self._append_textbox(self.cnki_output, str(text))
            elif target == "project":
                self._append_textbox(self.project_output, str(text))
            elif target == "remote":
                self._append_textbox(self.remote_output, str(text))
            elif target == "model":
                self._append_textbox(self.model_output, str(text))
            elif target == "task":
                self._append_textbox(self.task_output, str(text))
        elif isinstance(payload, Path):
            self._append_log(f"输出目录: {payload}")
        elif isinstance(payload, str):
            self._append_log(payload)
        elif payload is not None:
            self._append_log(str(payload))

    def _handle_task_error(self, message: str) -> None:
        self._set_busy(False, "失败")
        self._append_log(f"错误: {message}")
        if messagebox is not None:
            messagebox.showerror("Workbench 错误", message)

    def _topic(self) -> str:
        return self.topic_entry.get().strip()

    def _limit(self) -> int:
        try:
            return max(1, int(self.search_limit.get().strip() or "10"))
        except ValueError:
            return 10

    def _open_cnki(self) -> None:
        topic = self._topic() or self.cnki_topic_entry.get().strip()
        self._append_log(f"打开 CNKI: {self.controller.cnki_url(topic)}")
        webbrowser.open(self.controller.cnki_url(topic))

    def _on_search(self) -> None:
        topic = self._topic() or self.search_entry.get().strip()
        if not topic:
            self._queue_log("请输入检索关键词。")
            return
        limit = self._limit()

        def _work() -> tuple[str, str]:
            papers = self.controller.search(topic, limit=limit)
            lines = [f"检索主题: {topic}", f"结果数量: {len(papers)}", ""]
            for idx, paper in enumerate(papers, start=1):
                lines.append(f"{idx}. {paper.title}")
                meta = " | ".join(
                    str(x) for x in (paper.year or "", paper.source, paper.url) if str(x).strip()
                )
                if meta:
                    lines.append(f"   {meta}")
                if paper.abstract:
                    lines.append(f"   {paper.abstract[:220]}")
            lines.append("")
            lines.append(f"CNKI: {self.controller.cnki_url(topic)}")
            return ("search", "\n".join(lines))

        self._start_task(f"检索中: {topic}", _work)

    def _on_import_cnki(self) -> None:
        raw = self.cnki_paths_entry.get().strip()
        if not raw and filedialog is not None:
            picked = filedialog.askopenfilenames(
                title="选择 CNKI 导出的 RIS/BibTeX/TXT/PDF 文件",
                filetypes=[
                    ("CNKI metadata", "*.ris *.bib *.txt *.enw"),
                    ("PDF", "*.pdf"),
                    ("All files", "*.*"),
                ],
            )
            raw = ";".join(picked)
        paths = [Path(p) for p in raw.split(";") if p.strip()]
        if not paths:
            self._queue_log("没有选择任何 CNKI 文件。")
            return

        def _work() -> tuple[str, str]:
            records = self.controller.import_cnki(paths)
            lines = [f"已导入 {len(records)} 条 CNKI 记录", ""]
            for idx, record in enumerate(records, start=1):
                lines.append(f"{idx}. {record.title}")
                meta = " | ".join(
                    str(x) for x in (", ".join(record.authors), record.year or "", record.venue, record.url or record.file_path) if str(x).strip()
                )
                if meta:
                    lines.append(f"   {meta}")
            return ("cnki", "\n".join(lines))

        self._start_task("导入 CNKI", _work)

    def _on_render_model_config(self) -> None:
        try:
            cfg = self.controller.build_model_config(
                mode=self.model_mode.get().strip(),
                provider=self.model_provider.get().strip(),
                base_url=self.model_base_url.get().strip(),
                model=self.model_model.get().strip(),
                api_key_env=self.model_key_env.get().strip(),
            )
            text = (
                f"provider: {cfg.provider}\n"
                f"base_url: {cfg.base_url}\n"
                f"api_key_env: {cfg.api_key_env}\n"
                f"primary_model: {cfg.primary_model}\n"
            )
            self._append_textbox(self.model_output, text)
        except Exception as exc:
            self._handle_task_error(str(exc))

    def _on_plan(self) -> None:
        topic = self._topic() or self.cnki_topic_entry.get().strip() or self.search_entry.get().strip()
        if not topic:
            self._queue_log("请输入题目。")
            return

        def _work() -> tuple[str, str]:
            plan = self.controller.create_project_plan(topic)
            text = [f"题目: {topic}", f"类型: {plan['project_type']}", "模块: " + ", ".join(plan["modules"]), str(plan["principle"])]
            return ("project", "\n".join(text))

        self._start_task("生成毕设计划", _work)

    def _on_run_pipeline(self) -> None:
        topic = self._topic() or self.cnki_topic_entry.get().strip() or self.search_entry.get().strip()
        if not topic:
            self._queue_log("请输入题目。")
            return
        output = self.output_entry.get().strip() or None

        def _work() -> tuple[str, str]:
            run_dir = self.controller.run_pipeline(
                topic=topic,
                output=output,
                provider=self.model_provider.get().strip(),
                model=self.model_model.get().strip(),
                api_key_env=self.model_key_env.get().strip(),
                base_url=self.model_base_url.get().strip(),
                model_mode=self.model_mode.get().strip(),
                experiment_mode=self.experiment_mode.get().strip(),
                progress_reporter=self._queue_log,
            )
            return ("task", f"Pipeline 已启动完成，输出目录: {run_dir}")

        self._start_task(f"运行 Pipeline: {topic}", _work)

    def _on_analyze_project(self) -> None:
        path = self.project_path_entry.get().strip()
        if not path and filedialog is not None:
            picked = filedialog.askdirectory(title="选择已有代码项目目录")
            path = picked or ""
        if not path:
            self._queue_log("没有选择项目目录。")
            return

        def _work() -> tuple[str, str]:
            report = self.controller.analyze_project(path)
            lines = [
                f"项目: {report.root}",
                f"类型: {report.project_type}",
                f"文件数: {report.file_count}",
                "语言: " + (", ".join(report.languages) if report.languages else "unknown"),
                "建议章节: " + ", ".join(report.suggested_sections),
            ]
            return ("project", "\n".join(lines))

        self._start_task("分析项目", _work)

    def _collect_remote_profile(self) -> RemoteProfile:
        command = self.remote_ssh.get().strip()
        platform = self.remote_platform.get().strip()
        user = self.remote_user.get().strip()
        host = self.remote_host.get().strip()
        try:
            port = int(self.remote_port.get().strip() or "22")
        except ValueError:
            port = 22
        key_path = self.remote_key.get().strip()
        password = self.remote_password.get().strip()
        # Default remote Linux workdir.
        remote_workdir = self.remote_workdir.get().strip() or "/tmp/researchclaw_experiments"  # nosec B108
        gpu = ""
        base = self.controller.parse_remote_profile(
            command or f"ssh {user or 'root'}@{host or 'localhost'}",
            platform=platform,
            password=password,
            key_path=key_path,
            remote_workdir=remote_workdir,
        )
        return replace(
            base,
            user=user or base.user,
            host=host or base.host,
            port=port or base.port,
            remote_workdir=remote_workdir,
            gpu=gpu,
            password=password,
            key_path=key_path,
        )

    def _on_remote_test(self) -> None:
        profile = self._collect_remote_profile()

        def _work() -> tuple[str, str]:
            result = self.controller.test_remote(profile)
            text = self._format_remote_result("连接测试", profile, result)
            return ("remote", text)

        self._start_task("测试远程连接", _work)

    def _on_remote_upload(self) -> None:
        profile = self._collect_remote_profile()
        local_dir = self.remote_local.get().strip()
        if not local_dir and filedialog is not None:
            picked = filedialog.askdirectory(title="选择要上传的本地目录")
            local_dir = picked or ""
        if not local_dir:
            self._queue_log("没有选择本地目录。")
            return

        def _work() -> tuple[str, str]:
            result = self.controller.upload_remote(profile, local_dir, profile.remote_workdir)
            return ("remote", self._format_remote_result("上传代码", profile, result))

        self._start_task("上传远程代码", _work)

    def _on_remote_run(self) -> None:
        profile = self._collect_remote_profile()
        command = self.remote_command.get().strip() or "python train.py"

        def _work() -> tuple[str, str]:
            result = self.controller.run_remote(profile, command, remote_dir=profile.remote_workdir)
            return ("remote", self._format_remote_result("远程运行", profile, result))

        self._start_task("远程运行", _work)

    def _on_remote_download(self) -> None:
        profile = self._collect_remote_profile()
        local_dir = self.remote_local.get().strip()
        if not local_dir and filedialog is not None:
            picked = filedialog.askdirectory(title="选择下载结果的本地目录")
            local_dir = picked or ""
        if not local_dir:
            self._queue_log("没有选择下载目录。")
            return

        def _work() -> tuple[str, str]:
            result = self.controller.download_remote(profile, profile.remote_workdir, local_dir)
            return ("remote", self._format_remote_result("下载结果", profile, result))

        self._start_task("下载结果", _work)

    def _format_remote_result(self, action: str, profile: RemoteProfile, result) -> str:
        lines = [
            f"{action}: {profile.platform}",
            f"host: {profile.user}@{profile.host}:{profile.port}",
            f"auth: {profile.auth_method}",
            f"success: {result.success}",
        ]
        if result.stdout:
            lines.append("")
            lines.append(result.stdout.strip())
        if result.stderr:
            lines.append("")
            lines.append(f"stderr: {result.stderr.strip()}")
        return "\n".join(lines)

    def _browse_cnki_files(self) -> None:
        if filedialog is None:
            return
        picked = filedialog.askopenfilenames(
            title="选择 CNKI 导出的文件",
            filetypes=[
                ("CNKI metadata", "*.ris *.bib *.txt *.enw"),
                ("PDF", "*.pdf"),
                ("All files", "*.*"),
            ],
        )
        if picked:
            self.cnki_paths_entry.delete(0, "end")
            self.cnki_paths_entry.insert(0, ";".join(picked))

    def _browse_project_dir(self) -> None:
        if filedialog is None:
            return
        picked = filedialog.askdirectory(title="选择已有代码项目目录")
        if picked:
            self.project_path_entry.delete(0, "end")
            self.project_path_entry.insert(0, picked)


def create_app() -> Any:
    """Create the desktop app or raise a clear dependency error."""
    return WorkbenchApp()


def main() -> int:
    app = create_app()
    app.mainloop()
    return 0
