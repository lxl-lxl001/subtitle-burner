"""tkinter 图形界面。

设计目标：
- 用户只需 选择视频 → 确认自动匹配的字幕 → 选输出目录 → 开始；
- 所有耗时操作（ffprobe、ffmpeg）都不阻塞界面；
- 支持进度、取消、错误原文展示与日志入口；
- 烧录期间禁止修改参数，避免界面显示与实际任务不一致。
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import core

VIDEO_FILETYPES = [
    (
        "视频文件",
        "*.mp4 *.mkv *.avi *.mov *.m4v *.wmv *.flv *.webm *.ts *.m2ts *.mpg *.mpeg *.3gp",
    ),
    ("所有文件", "*.*"),
]
SUBTITLE_FILETYPES = [
    ("字幕文件", "*.srt *.ass"),
    ("SRT 字幕", "*.srt"),
    ("ASS 字幕", "*.ass"),
    ("所有文件", "*.*"),
]


def _clean_path_text(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


class BurnApp:
    def __init__(
        self,
        root: tk.Tk,
        paths: core.AppPaths,
        logger: logging.Logger,
        config_store: core.ConfigStore,
        settings: dict[str, Any],
        startup_warnings: list[str] | None = None,
    ):
        self.root = root
        self.paths = paths
        self.logger = logger
        self.config_store = config_store
        self.settings = settings or {}
        self.startup_warnings = list(startup_warnings or [])
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()

        self._probe_seq = 0
        self._job_token = 0
        self._running = False
        self._starting = False
        self._closing = False
        self._current_info: core.VideoInfo | None = None
        self._subtitle_choices: list[str] = []
        self._run_warnings: list[str] = []
        self._last_video_dir = str(self.settings.get("last_video_dir") or "")
        self._config_warning_shown = False
        self._log_button_available = True
        self._active_job: core.BurnJob | None = None

        self.tools_ready = not core.missing_tools(paths)

        self._configure_window()
        self._build_ui()
        self._apply_settings()
        self._refresh_output_preview()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._tk_callback_exception
        self.root.after(80, self._poll_events)
        self.root.after(250, self._after_startup)

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------

    def _configure_window(self) -> None:
        self.root.title(f"{core.APP_NAME}  v{core.APP_VERSION}")
        self.root.geometry("900x790")
        self.root.minsize(820, 720)
        style = ttk.Style(self.root)
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                    break
                except tk.TclError:
                    continue

        base_font = ("Microsoft YaHei UI", 9)
        style.configure(".", font=base_font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5f6368")
        style.configure("SectionHint.TLabel", foreground="#6b7075")
        style.configure("Warn.TLabel", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Info.TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", foreground="#1f5f99")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"))
        self.root.option_add("*TCombobox*Listbox.font", base_font)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(18, 14, 18, 12))
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text=core.APP_NAME, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            outer,
            text="把 SRT / ASS 字幕永久烧进画面，输出视频双击即可观看，无需外挂字幕。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        # 1. 视频
        video_box = ttk.LabelFrame(outer, text=" 1. 视频文件 ", padding=(10, 8))
        video_box.grid(row=2, column=0, sticky="ew")
        video_box.columnconfigure(0, weight=1)
        self.video_var = tk.StringVar()
        self.video_entry = ttk.Entry(video_box, textvariable=self.video_var)
        self.video_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.video_button = ttk.Button(
            video_box, text="选择视频…", width=13, command=self._choose_video
        )
        self.video_button.grid(row=0, column=1)

        # 2. 字幕
        subtitle_box = ttk.LabelFrame(
            outer, text=" 2. 字幕（同目录同名 .srt / .ass，SRT 优先） ", padding=(10, 8)
        )
        subtitle_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        subtitle_box.columnconfigure(0, weight=1)
        self.subtitle_var = tk.StringVar()
        self.sub_combo = ttk.Combobox(
            subtitle_box,
            textvariable=self.subtitle_var,
            state="readonly",
            height=6,
        )
        self.sub_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.sub_browse_button = ttk.Button(
            subtitle_box, text="手动选择…", width=13, command=self._choose_subtitle
        )
        self.sub_browse_button.grid(row=0, column=1, padx=(0, 6))
        self.sub_auto_button = ttk.Button(
            subtitle_box, text="重新匹配", width=10, command=self._auto_match_clicked
        )
        self.sub_auto_button.grid(row=0, column=2)
        self.sub_hint_var = tk.StringVar(value="选择视频后会自动查找同名字幕。")
        ttk.Label(
            subtitle_box,
            textvariable=self.sub_hint_var,
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))

        # 3. 输出目录
        output_box = ttk.LabelFrame(outer, text=" 3. 输出目录 ", padding=(10, 8))
        output_box.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        output_box.columnconfigure(0, weight=1)
        self.output_dir_var = tk.StringVar()
        self.output_entry = ttk.Entry(output_box, textvariable=self.output_dir_var)
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.output_entry.bind("<FocusOut>", lambda _event: self._refresh_output_preview())
        self.output_entry.bind("<Return>", lambda _event: self._refresh_output_preview())
        self.output_button = ttk.Button(
            output_box, text="选择目录…", width=13, command=self._choose_output_dir
        )
        self.output_button.grid(row=0, column=1)
        self.output_preview_var = tk.StringVar(value="")
        ttk.Label(
            output_box,
            textvariable=self.output_preview_var,
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))

        # 视频信息
        info_box = ttk.LabelFrame(outer, text=" 视频信息 ", padding=(10, 7))
        info_box.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        info_box.columnconfigure(0, weight=1)
        self.info_var = tk.StringVar(value="尚未选择视频。")
        ttk.Label(
            info_box,
            textvariable=self.info_var,
            justify="left",
            anchor="w",
            wraplength=820,
        ).grid(row=0, column=0, sticky="ew")
        self.hdr_var = tk.StringVar(value="")
        self.hdr_label = ttk.Label(
            info_box,
            textvariable=self.hdr_var,
            style="Warn.TLabel",
            justify="left",
            wraplength=820,
        )
        self.hdr_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # 参数
        options_box = ttk.LabelFrame(outer, text=" 烧录参数 ", padding=(10, 8))
        options_box.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        options_box.columnconfigure(3, weight=1)

        ttk.Label(options_box, text="CRF：").grid(row=0, column=0, sticky="w")
        self.crf_var = tk.StringVar(value=str(core.DEFAULT_CRF))
        self.crf_spin = ttk.Spinbox(
            options_box, from_=core.MIN_CRF, to=core.MAX_CRF, increment=1,
            textvariable=self.crf_var, width=6
        )
        self.crf_spin.grid(row=0, column=1, sticky="w", padx=(0, 18))
        ttk.Label(options_box, text="字体：").grid(row=0, column=2, sticky="w")
        self.font_var = tk.StringVar(value=core.DEFAULT_FONT_NAME)
        self.font_entry = ttk.Entry(options_box, textvariable=self.font_var, width=28)
        self.font_entry.grid(row=0, column=3, sticky="ew", padx=(0, 18))
        ttk.Label(options_box, text="颜色：").grid(row=0, column=4, sticky="w")
        self.color_var = tk.StringVar(value=core.DEFAULT_COLOR_NAME)
        self.color_combo = ttk.Combobox(
            options_box,
            textvariable=self.color_var,
            state="readonly",
            values=list(core.PRIMARY_COLOURS.keys()),
            width=8,
        )
        self.color_combo.grid(row=0, column=5, sticky="w")

        ttk.Label(options_box, text="字号：").grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.size_var = tk.StringVar(value=str(core.DEFAULT_FONT_SIZE))
        self.size_spin = ttk.Spinbox(
            options_box, from_=6, to=120, increment=1, textvariable=self.size_var, width=6
        )
        self.size_spin.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(7, 0))
        ttk.Label(options_box, text="底部边距：").grid(row=1, column=2, sticky="w", pady=(7, 0))
        self.margin_var = tk.StringVar(value=str(core.DEFAULT_MARGIN_V))
        self.margin_spin = ttk.Spinbox(
            options_box, from_=0, to=500, increment=1, textvariable=self.margin_var, width=6
        )
        self.margin_spin.grid(row=1, column=3, sticky="w", padx=(0, 18), pady=(7, 0))
        ttk.Label(
            options_box,
            text=(
                "CRF 越小画质越高、文件越大，默认 18。SRT 使用以上字体样式（字体请填英文注册名）；"
                "ASS 完整保留原样式。"
            ),
            style="SectionHint.TLabel",
        ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(7, 0))

        # 进度与操作
        action_box = ttk.Frame(outer)
        action_box.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        action_box.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(
            action_box, mode="determinate", maximum=100, value=0
        )
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(action_box, textvariable=self.percent_var, width=7, anchor="e").grid(
            row=0, column=1
        )

        self.status_var = tk.StringVar(value="请选择视频文件开始。")
        self.status_label = ttk.Label(
            action_box, textvariable=self.status_var, style="Status.TLabel", wraplength=680
        )
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        button_bar = ttk.Frame(action_box)
        button_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        button_bar.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(
            button_bar,
            text="开始烧录",
            width=18,
            style="Primary.TButton",
            command=self._start_burn,
        )
        self.start_button.grid(row=0, column=1, padx=(0, 8))
        self.cancel_button = ttk.Button(
            button_bar,
            text="取消",
            width=12,
            command=self._cancel_burn,
            state="disabled",
        )
        self.cancel_button.grid(row=0, column=2)

        footer = ttk.Frame(outer)
        footer.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text="全程本地处理，不会上传视频。输出文件名固定为“原文件名_硬字幕.原扩展名”。",
            style="SectionHint.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.logs_button = ttk.Button(
            footer, text="打开日志目录", width=13, command=self._open_logs
        )
        self.logs_button.grid(row=0, column=1, sticky="e")

        if not self.tools_ready:
            self.start_button.configure(state="disabled")
            self.status_var.set("缺少 ffmpeg.exe 或 ffprobe.exe，暂时无法开始。")

    def _apply_settings(self) -> None:
        def int_setting(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(self.settings.get(name, default))
            except (TypeError, ValueError):
                value = default
            return min(max(value, low), high)

        self.crf_var.set(str(int_setting("crf", core.DEFAULT_CRF, core.MIN_CRF, core.MAX_CRF)))
        self.size_var.set(str(int_setting("font_size", core.DEFAULT_FONT_SIZE, 6, 120)))
        self.margin_var.set(
            str(int_setting("margin_v", core.DEFAULT_MARGIN_V, 0, 500))
        )
        font = str(self.settings.get("font_name") or core.DEFAULT_FONT_NAME).strip()
        self.font_var.set(font)
        color = str(self.settings.get("primary_color") or core.DEFAULT_COLOR_NAME)
        if color not in core.PRIMARY_COLOURS:
            color = core.DEFAULT_COLOR_NAME
        self.color_var.set(color)
        last_output = str(self.settings.get("last_output_dir") or "")
        if last_output:
            self.output_dir_var.set(last_output)

    # ------------------------------------------------------------------
    # 窗口状态与基础交互
    # ------------------------------------------------------------------

    def _editable_widgets(self) -> list[Any]:
        return [
            self.video_button,
            self.sub_browse_button,
            self.sub_auto_button,
            self.output_entry,
            self.output_button,
            self.crf_spin,
            self.font_entry,
            self.color_combo,
            self.size_spin,
            self.margin_spin,
        ]

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self._editable_widgets():
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        try:
            self.sub_combo.configure(state="readonly" if enabled else "disabled")
            self.color_combo.configure(state="readonly" if enabled else "disabled")
        except tk.TclError:
            pass
        if enabled:
            if self.tools_ready:
                self.start_button.configure(state="normal")
        else:
            self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")

    def _begin_busy(self) -> None:
        self._set_controls_enabled(False)
        self.root.configure(cursor="watch")

    def _end_busy_without_job(self) -> None:
        self.root.configure(cursor="")
        self._set_controls_enabled(True)

    def _start_progress(self, duration: float | None) -> None:
        self.percent_var.set("0%")
        if duration and duration > 0:
            self.progress.configure(mode="determinate", maximum=100, value=0)
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        self.status_var.set("正在准备…")

    def _stop_progress_indeterminate(self) -> None:
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.progress.configure(mode="determinate", maximum=100)

    def _format_seconds(self, seconds: float) -> str:
        if not seconds or seconds < 0:
            seconds = 0
        total = int(seconds)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ------------------------------------------------------------------
    # 选择文件与自动匹配
    # ------------------------------------------------------------------

    def _choose_video(self) -> None:
        if self._running or self._starting:
            return
        initial = self._last_video_dir
        if not initial:
            current = _clean_path_text(self.video_var.get())
            if current:
                initial = str(Path(current).parent)
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择视频文件",
            initialdir=initial or None,
            filetypes=VIDEO_FILETYPES,
        )
        if not path:
            return
        self._last_video_dir = str(Path(path).parent)
        self._set_video(Path(path))

    def _set_video(self, video: Path) -> None:
        self.video_var.set(str(video))
        if not _clean_path_text(self.output_dir_var.get()):
            self.output_dir_var.set(str(video.parent))
        self._auto_match_subtitles(show_feedback=False)
        self._refresh_output_preview()
        self._probe_async(video)

    def _choose_subtitle(self) -> None:
        if self._running or self._starting:
            return
        initial = ""
        current = _clean_path_text(self.subtitle_var.get())
        if current:
            initial = str(Path(current).parent)
        elif self.video_var.get().strip():
            initial = str(Path(_clean_path_text(self.video_var.get())).parent)
        path = filedialog.askopenfilename(
            parent=self.root,
            title="手动选择字幕文件",
            initialdir=initial or None,
            filetypes=SUBTITLE_FILETYPES,
        )
        if not path:
            return
        chosen = str(Path(path))
        if chosen not in self._subtitle_choices:
            self._subtitle_choices.append(chosen)
            self.sub_combo.configure(values=self._subtitle_choices)
        self.subtitle_var.set(chosen)
        self.sub_hint_var.set(f"已手动选择：{Path(path).name}")

    def _auto_match_subtitles(self, show_feedback: bool) -> None:
        video_text = _clean_path_text(self.video_var.get())
        if not video_text:
            if show_feedback:
                messagebox.showwarning(core.APP_NAME, "请先选择视频文件。", parent=self.root)
            return
        video = Path(video_text)
        matches = core.find_matching_subtitles(video)
        self._subtitle_choices = [str(path) for path in matches]
        self.sub_combo.configure(values=self._subtitle_choices)
        if not matches:
            self.subtitle_var.set("")
            self.sub_hint_var.set(
                "未找到同名的 .srt / .ass，请点击“手动选择…”指定字幕文件。"
            )
            if show_feedback:
                messagebox.showinfo(
                    core.APP_NAME,
                    "未在视频同目录找到同主文件名的 .srt 或 .ass 字幕。\n\n"
                    "请确认字幕与视频同名，或手动选择字幕。",
                    parent=self.root,
                )
            return

        self.subtitle_var.set(str(matches[0]))
        if len(matches) > 1:
            kinds = "、".join(path.suffix.lower() for path in matches)
            self.sub_hint_var.set(
                f"自动匹配到 {len(matches)} 个字幕（{kinds}），默认优先 SRT，可下拉切换。"
            )
        else:
            self.sub_hint_var.set(f"已自动匹配：{matches[0].name}")
        if show_feedback:
            messagebox.showinfo(
                core.APP_NAME,
                f"找到 {len(matches)} 个字幕文件，已选择：\n{matches[0].name}",
                parent=self.root,
            )

    def _auto_match_clicked(self) -> None:
        self._auto_match_subtitles(show_feedback=True)

    def _choose_output_dir(self) -> None:
        if self._running or self._starting:
            return
        initial = _clean_path_text(self.output_dir_var.get())
        if not initial:
            video_text = _clean_path_text(self.video_var.get())
            if video_text:
                initial = str(Path(video_text).parent)
        path = filedialog.askdirectory(
            parent=self.root,
            title="选择输出目录",
            initialdir=initial or None,
            mustexist=False,
        )
        if not path:
            return
        self.output_dir_var.set(str(Path(path)))
        self._refresh_output_preview()

    def _refresh_output_preview(self) -> None:
        video_text = _clean_path_text(self.video_var.get())
        output_text = _clean_path_text(self.output_dir_var.get())
        if not video_text or not output_text:
            self.output_preview_var.set(
                "输出文件名将自动生成为：原文件名_硬字幕.原扩展名"
            )
            return
        try:
            target = core.default_output_path(video_text, output_text)
            if target.exists():
                self.output_preview_var.set(
                    f"输出文件名：{target.name}（同名文件已存在，开始时会询问是否覆盖）"
                )
            else:
                self.output_preview_var.set(f"输出文件名：{target.name}")
        except (OSError, ValueError):
            self.output_preview_var.set(
                "输出文件名将自动生成为：原文件名_硬字幕.原扩展名"
            )

    # ------------------------------------------------------------------
    # 视频信息探测
    # ------------------------------------------------------------------

    def _probe_async(self, video: Path) -> None:
        self._probe_seq += 1
        seq = self._probe_seq
        self._current_info = None
        self.info_var.set("正在读取视频信息…")
        self.hdr_var.set("")

        def worker() -> None:
            try:
                info = core.probe_video(self.paths.ffprobe, video)
            except core.BurnError as exc:
                self.events.put(("probe_error", seq, str(video), exc.message, exc.details))
            except Exception as exc:  # 不让后台线程静默崩溃
                self.logger.exception("探测视频信息失败")
                self.events.put(
                    ("probe_error", seq, str(video), "读取视频信息失败。", str(exc))
                )
            else:
                self.events.put(("probe_ok", seq, str(video), info))

        threading.Thread(target=worker, name="probe-video", daemon=True).start()

    def _handle_probe_ok(self, seq: int, video_text: str, info: core.VideoInfo) -> None:
        if seq != self._probe_seq or video_text != _clean_path_text(self.video_var.get()):
            return
        self._current_info = info
        self.info_var.set(info.summary_text())
        if info.hdr:
            self.hdr_var.set(
                "⚠ 检测到 HDR："
                + info.hdr_text()
                + "。重新编码为 H.264 后可能出现色彩偏差，开始前会再次确认。"
            )
        else:
            self.hdr_var.set("")
        if info.rotation:
            self.hdr_var.set(
                (self.hdr_var.get() + "  " if self.hdr_var.get() else "")
                + f"⚠ 视频含旋转元数据（{info.rotation}°），播放方向以播放器为准。"
            )

    def _handle_probe_error(self, seq: int, video_text: str, message: str, details: str) -> None:
        if seq != self._probe_seq or video_text != _clean_path_text(self.video_var.get()):
            return
        self.info_var.set(f"无法读取视频信息：{message}\n点击开始时还会重新检测。")
        self.hdr_var.set("")
        self.logger.warning("探测视频信息失败：%s\n%s", message, details)

    # ------------------------------------------------------------------
    # 开始任务
    # ------------------------------------------------------------------

    def _start_burn(self) -> None:
        if self._running or self._starting or self._closing:
            return
        if not self.tools_ready:
            self._show_error_dialog(
                "缺少运行文件",
                "未找到 ffmpeg.exe 或 ffprobe.exe。",
                "请确认它们与程序位于同一文件夹：\n" + str(self.paths.base_dir),
            )
            return

        self._starting = True
        self._begin_busy()
        self.status_var.set("正在检查文件…")
        launched = False
        try:
            video_path = _clean_path_text(self.video_var.get())
            subtitle_path = _clean_path_text(self.subtitle_var.get())
            if not video_path:
                raise core.BurnError("请先选择视频文件。")
            video = Path(video_path)
            if not video.is_file():
                raise core.BurnError(f"视频文件不存在：\n{video}")
            if not subtitle_path:
                raise core.BurnError(
                    "字幕不存在或未选择。\n\n"
                    "请选择同名的 .srt/.ass 字幕，或点击“手动选择…”。"
                )
            subtitle = Path(subtitle_path)
            if not subtitle.is_file():
                raise core.BurnError(f"字幕文件不存在：\n{subtitle}")
            if subtitle.suffix.casefold() not in core.SUBTITLE_EXTENSIONS:
                raise core.BurnError("字幕格式不支持，只支持 .srt 和 .ass。")

            output_dir = self._resolve_output_dir()
            if output_dir is None:
                return

            crf, style = self._collect_style()
            self.status_var.set("正在读取视频信息…")
            self.root.update_idletasks()
            info = core.probe_video(self.paths.ffprobe, video)
            self._current_info = info
            self.info_var.set(info.summary_text())
            if info.hdr:
                self.hdr_var.set(
                    "⚠ 检测到 HDR：" + info.hdr_text() + "。编码后可能出现色彩偏差。"
                )
                if not messagebox.askokcancel(
                    "HDR 视频提示",
                    "检测到 HDR 视频："
                    + info.hdr_text()
                    + "\n\n本工具会重新编码为 H.264，HDR 转 SDR 可能产生色彩偏差。"
                    "\n普通 SDR 视频不受影响。\n\n是否继续？",
                    parent=self.root,
                    icon="warning",
                ):
                    self.status_var.set("已取消。")
                    return
            else:
                self.hdr_var.set("")

            if not 0 <= crf <= 51:
                raise core.BurnError("CRF 必须在 0 到 51 之间。")

            output_path, overwrite = self._resolve_output_and_collision(video, output_dir)
            if output_path is None:
                self.status_var.set("已取消。")
                return
            if core.same_path(output_path, video):
                raise core.BurnError("输出路径与原视频相同，已阻止覆盖原视频。")

            self._save_settings(show_warning=True)
            self._run_warnings.clear()
            self._job_token += 1
            token = self._job_token
            self._running = True
            self._starting = False
            self._start_progress(info.duration)
            self.progress.configure(value=0)
            self.percent_var.set("0%")
            self.status_var.set(f"即将输出：{output_path.name}")
            self.cancel_button.configure(state="normal")
            self.root.configure(cursor="")
            self.output_preview_var.set(f"正在输出：{output_path.name}")

            job = core.BurnJob(
                ffmpeg=self.paths.ffmpeg,
                ffprobe=self.paths.ffprobe,
                video_path=video,
                subtitle_path=subtitle,
                output_path=output_path,
                base_dir=self.paths.base_dir,
                video_info=info,
                crf=crf,
                style=style,
                overwrite=overwrite,
                logger=self.logger,
                progress_callback=lambda seconds, duration: self.events.put(
                    ("progress", token, seconds, duration)
                ),
                status_callback=lambda text: self.events.put(("status", token, text)),
                warning_callback=lambda text: self.events.put(("warning", token, text)),
            )
            self.logger.info(
                "用户开始任务：video=%s subtitle=%s output=%s overwrite=%s crf=%s",
                video,
                subtitle,
                output_path,
                overwrite,
                crf,
            )
            self._active_job = job
            threading.Thread(
                target=self._worker,
                args=(job, token),
                name="burn-job",
                daemon=True,
            ).start()
            launched = True
        except core.BurnError as exc:
            self.status_var.set("未开始：" + exc.message.splitlines()[0])
            self._show_error_dialog("无法开始烧录", exc.message, exc.details)
        except OSError as exc:
            self.logger.exception("开始任务时发生文件系统错误")
            self.status_var.set("无法开始烧录。")
            self._show_error_dialog("无法开始烧录", str(exc))
        except Exception as exc:
            self.logger.exception("开始任务时发生未预期错误")
            self.status_var.set("发生未预期错误。")
            self._show_error_dialog(
                "程序错误", "发生未预期的错误。", f"{exc}\n\n详情请查看日志。"
            )
        finally:
            if not launched:
                # 极端情况下（例如线程创建失败）_running 可能已置位，必须恢复界面。
                if self._running:
                    self._running = False
                    self._active_job = None
                    self._stop_progress_indeterminate()
                    self.progress.configure(value=0)
                    self.percent_var.set("0%")
                    self.cancel_button.configure(state="disabled")
                self._starting = False
                self._end_busy_without_job()

    def _resolve_output_dir(self) -> Path | None:
        raw = _clean_path_text(self.output_dir_var.get())
        if not raw:
            raise core.BurnError("请选择输出目录。")
        try:
            output_dir = Path(os.path.abspath(os.path.expanduser(raw)))
        except (OSError, ValueError) as exc:
            raise core.BurnError("输出目录路径无效。", f"{raw}\n{exc}") from exc
        if not output_dir.exists():
            if not messagebox.askyesno(
                core.APP_NAME,
                f"输出目录不存在：\n{output_dir}\n\n是否创建？",
                parent=self.root,
            ):
                return None
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise core.BurnError(
                    "无法创建输出目录。", f"{output_dir}\n{exc}"
                ) from exc
        if not output_dir.is_dir():
            raise core.BurnError(f"输出路径不是目录：\n{output_dir}")
        self.output_dir_var.set(str(output_dir))
        return output_dir

    def _collect_style(self) -> tuple[int, core.SubtitleStyle]:
        try:
            crf = int(self.crf_var.get().strip())
        except ValueError as exc:
            raise core.BurnError("CRF 必须是 0 到 51 之间的整数。") from exc
        if not core.MIN_CRF <= crf <= core.MAX_CRF:
            raise core.BurnError(
                f"CRF 必须在 {core.MIN_CRF} 到 {core.MAX_CRF} 之间。"
            )
        font_name = self.font_var.get().strip()
        if not font_name:
            raise core.BurnError("字体名称不能为空。")
        if any(ord(ch) >= 128 for ch in font_name):
            raise core.BurnError(
                "字体名称请填写英文字体名。",
                "为保证 Windows 下 ffmpeg 滤镜参数为纯 ASCII，"
                "请使用 Microsoft YaHei、SimHei、SimSun 等英文注册名。",
            )
        try:
            font_size = int(self.size_var.get().strip())
        except ValueError as exc:
            raise core.BurnError("字号必须是整数。") from exc
        if not 6 <= font_size <= 120:
            raise core.BurnError("字号必须在 6 到 120 之间。")
        try:
            margin_v = int(self.margin_var.get().strip())
        except ValueError as exc:
            raise core.BurnError("底部边距必须是整数。") from exc
        if not 0 <= margin_v <= 500:
            raise core.BurnError("底部边距必须在 0 到 500 之间。")
        color_name = self.color_var.get()
        if color_name not in core.PRIMARY_COLOURS:
            color_name = core.DEFAULT_COLOR_NAME
        style = core.SubtitleStyle(
            font_name=font_name,
            font_size=font_size,
            margin_v=margin_v,
            primary_colour=core.PRIMARY_COLOURS[color_name],
        ).normalized()
        return crf, style

    def _resolve_output_and_collision(
        self, video: Path, output_dir: Path
    ) -> tuple[Path | None, bool]:
        base = core.default_output_path(video, output_dir)
        if base.exists() and not base.is_file():
            raise core.BurnError(
                "输出文件名被一个目录占用，无法写入。",
                f"{base}\n\n请换一个输出目录。",
            )
        if core.same_path(base, video):
            return core.choose_output_path(video, output_dir, overwrite=False), False
        if base.exists():
            overwrite = messagebox.askyesno(
                "输出文件已存在",
                f"输出目录中已存在同名文件：\n{base.name}\n\n"
                "是否覆盖它？\n\n"
                "选择“是”：覆盖已有文件\n"
                "选择“否”：自动追加 _1、_2 等序号",
                parent=self.root,
                icon="warning",
            )
            if overwrite:
                return base, True
            chosen = core.choose_output_path(video, output_dir, overwrite=False)
            return chosen, False
        return base, False

    # ------------------------------------------------------------------
    # 后台任务与事件
    # ------------------------------------------------------------------

    def _worker(self, job: core.BurnJob, token: int) -> None:
        try:
            result = core.run_burn_job(job)
        except core.BurnCancelled:
            self.events.put(("finished", token, "cancelled", None))
        except core.BurnError as exc:
            self.events.put(("finished", token, "error", (exc.message, exc.details)))
        except Exception as exc:
            self.logger.exception("烧录线程发生未捕获异常")
            self.events.put(
                (
                    "finished",
                    token,
                    "error",
                    ("发生未预期的错误。", f"{exc}\n\n详情请查看日志。"),
                )
            )
        else:
            self.events.put(("finished", token, "success", result))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            if self.root.winfo_exists():
                self.root.after(80, self._poll_events)
        except tk.TclError:
            pass

    def _handle_event(self, event: tuple[Any, ...]) -> None:
        kind = event[0]
        if kind == "probe_ok":
            self._handle_probe_ok(event[1], event[2], event[3])
            return
        if kind == "probe_error":
            self._handle_probe_error(event[1], event[2], event[3], event[4])
            return
        if len(event) < 2 or event[1] != self._job_token:
            return
        if kind == "progress":
            self._handle_progress(event[2], event[3])
        elif kind == "status":
            self.status_var.set(event[2])
        elif kind == "warning":
            self._run_warnings.append(str(event[2]))
            self.logger.warning("任务警告：%s", event[2])
        elif kind == "finished":
            self._handle_finished(event[2], event[3])

    def _handle_progress(self, seconds: float, duration: float | None) -> None:
        if not self._running:
            return
        duration_value = duration or 0
        if duration_value > 0:
            percent = max(0.0, min(99.9, seconds / duration_value * 100))
            try:
                self.progress.stop()
            except tk.TclError:
                pass
            self.progress.configure(mode="determinate", maximum=100)
            self.progress.configure(value=percent)
            self.percent_var.set(f"{percent:.1f}%")
            self.status_var.set(
                f"正在编码…  已处理 {self._format_seconds(seconds)} / "
                f"{self._format_seconds(duration_value)}"
            )
        else:
            self.status_var.set(f"正在编码…  已处理 {self._format_seconds(seconds)}")

    def _handle_finished(self, kind: str, payload: Any) -> None:
        self._running = False
        self.cancel_button.configure(state="disabled")
        self.root.configure(cursor="")
        self._stop_progress_indeterminate()
        self._set_controls_enabled(True)

        if kind == "cancelled":
            self.progress.configure(value=0)
            self.percent_var.set("0%")
            self.status_var.set("任务已取消，临时文件已清理。")
            self.output_preview_var.set("")
            self._refresh_output_preview()
            if not self._closing:
                self._show_run_warnings()
                messagebox.showinfo(
                    core.APP_NAME,
                    "任务已取消，未完成的临时文件已清理。",
                    parent=self.root,
                )
            self._destroy_if_closing()
            return

        if kind == "error":
            message, details = payload
            self.status_var.set("烧录失败。")
            if not self._closing:
                self._show_run_warnings()
                self._show_error_dialog("烧录失败", message, details)
            self._refresh_output_preview()
            self._destroy_if_closing()
            return

        # success
        result: core.BurnResult = payload
        self.progress.configure(value=100)
        self.percent_var.set("100%")
        self.status_var.set("烧录完成 ✓")
        self.output_preview_var.set(f"已输出：{result.output_path.name}")
        if not self._closing:
            self._show_run_warnings()
            open_folder = messagebox.askyesno(
                "烧录完成",
                "字幕已经烧进画面，输出视频可以直接双击观看，不再需要字幕文件。\n\n"
                f"输出位置：\n{result.output_path}\n\n"
                f"耗时：{result.elapsed_seconds:.1f} 秒\n\n"
                "是否打开输出文件夹？",
                parent=self.root,
                icon="info",
            )
            if open_folder:
                self._open_folder(result.output_path.parent)
        self._destroy_if_closing()

    def _show_run_warnings(self) -> None:
        if not self._run_warnings:
            return
        warnings = self._run_warnings
        self._run_warnings = []
        text = "\n\n".join(dict.fromkeys(warnings))
        messagebox.showwarning("任务提醒", text, parent=self.root)

    def _cancel_burn(self) -> None:
        if not self._running:
            return
        self.cancel_button.configure(state="disabled")
        self.status_var.set("正在取消，请稍候…")
        # 取消事件由核心线程消费；这里不会阻塞 Tk 主循环。
        if self._active_job is not None:
            self._active_job.cancel_event.set()
            self.logger.info("用户请求取消任务")

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._starting:
            messagebox.showinfo(
                "请稍候",
                "正在检查文件或等待确认，请完成当前提示后再关闭窗口。",
                parent=self.root,
            )
            return
        if self._running:
            if not messagebox.askyesno(
                "正在烧录",
                "任务仍在进行。是否取消任务并退出？",
                parent=self.root,
                icon="warning",
            ):
                return
            self._closing = True
            if self._active_job is not None:
                self._active_job.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("正在取消并清理临时文件…")
            return
        self._save_settings(show_warning=False)
        self.root.destroy()

    def _destroy_if_closing(self) -> None:
        self._active_job = None
        if self._closing:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # 设置持久化与日志入口
    # ------------------------------------------------------------------

    def _settings_snapshot(self) -> dict[str, Any]:
        return {
            "version": core.APP_VERSION,
            "crf": _safe_int_text(self.crf_var.get(), core.DEFAULT_CRF),
            "font_name": self.font_var.get().strip() or core.DEFAULT_FONT_NAME,
            "font_size": _safe_int_text(self.size_var.get(), core.DEFAULT_FONT_SIZE),
            "margin_v": _safe_int_text(self.margin_var.get(), core.DEFAULT_MARGIN_V),
            "primary_color": self.color_var.get()
            if self.color_var.get() in core.PRIMARY_COLOURS
            else core.DEFAULT_COLOR_NAME,
            "last_output_dir": _clean_path_text(self.output_dir_var.get()),
            "last_video_dir": self._last_video_dir,
        }

    def _save_settings(self, show_warning: bool) -> None:
        try:
            self.config_store.save(self._settings_snapshot())
        except (OSError, TypeError, ValueError) as exc:
            if show_warning and not self._config_warning_shown:
                self._config_warning_shown = True
                messagebox.showwarning(
                    "配置无法保存",
                    "无法写入程序目录下的 config/settings.json。\n"
                    "本次设置仅在当前运行期间有效。\n\n"
                    f"路径：{self.config_store.path}\n原因：{exc}",
                    parent=self.root,
                )

    def _open_logs(self) -> None:
        self._open_folder(self.paths.logs_dir)

    def _open_folder(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["explorer", str(folder)])
        except OSError as exc:
            messagebox.showerror(
                "无法打开文件夹", f"{folder}\n\n{exc}", parent=self.root
            )

    # ------------------------------------------------------------------
    # 启动检查与错误展示
    # ------------------------------------------------------------------

    def _after_startup(self) -> None:
        warnings = list(self.startup_warnings)
        missing = core.missing_tools(self.paths)
        if missing:
            names = "、".join(path.name for path in missing)
            details = (
                "缺少：\n"
                + "\n".join(str(path) for path in missing)
                + "\n\n请把 ffmpeg.exe 和 ffprobe.exe（static 构建）与程序放在同一文件夹。\n"
                "程序目录："
                + str(self.paths.base_dir)
            )
            self._show_error_dialog("缺少运行所需的工具", f"未找到 {names}。", details)
            self.startup_warnings = []
            return
        if warnings:
            self.startup_warnings = []
            messagebox.showwarning(
                "启动提醒",
                "\n\n".join(warnings),
                parent=self.root,
            )

    def _show_error_dialog(self, title: str, message: str, details: str = "") -> None:
        if self._closing:
            return
        if not details:
            messagebox.showerror(title, message, parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry("780x480")
        dialog.minsize(620, 360)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        message_label = ttk.Label(
            dialog,
            text=message,
            padding=(16, 14, 16, 6),
            justify="left",
            wraplength=730,
        )
        message_label.grid(row=0, column=0, sticky="ew")

        text_frame = ttk.Frame(dialog, padding=(16, 0, 16, 8))
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        output = tk.Text(
            text_frame,
            wrap="word",
            height=12,
            font=("Consolas", 9),
            relief="solid",
            borderwidth=1,
        )
        output.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        output.configure(yscrollcommand=scrollbar.set)
        log_hint = (
            f"\n\n完整日志：{self.paths.logs_dir / 'app.log'}"
            if self._log_button_available
            else ""
        )
        output.insert("1.0", details + log_hint)
        output.configure(state="disabled")

        button_bar = ttk.Frame(dialog, padding=(16, 0, 16, 14))
        button_bar.grid(row=2, column=0, sticky="ew")
        button_bar.columnconfigure(0, weight=1)

        def copy_all() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(message + "\n\n" + details)
            copy_button.configure(text="已复制")

        copy_button = ttk.Button(button_bar, text="复制错误信息", command=copy_all)
        copy_button.grid(row=0, column=1, padx=(0, 8))
        if self._log_button_available:
            ttk.Button(
                button_bar, text="打开日志目录", command=self._open_logs
            ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(button_bar, text="关闭", command=dialog.destroy).grid(row=0, column=3)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()
        self.root.wait_window(dialog)

    def _tk_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        self.logger.error(
            "Tk 回调异常",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        try:
            messagebox.showerror(
                "程序错误",
                f"{exc_value}\n\n详情请查看日志：{self.paths.logs_dir / 'app.log'}",
                parent=self.root,
            )
        except tk.TclError:
            pass


def _safe_int_text(text: str, default: int) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default
