"""字幕烧录工具入口。

运行：
    python app.py

打包：
    pyinstaller --noconfirm --clean app.spec
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

import core
from gui import BurnApp


def _enable_high_dpi() -> None:
    """避免高分屏下 tkinter 字体和控件发虚。"""

    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


def main() -> None:
    _enable_high_dpi()
    paths = core.AppPaths.build()
    logger, log_warning = core.setup_logging(paths)
    warnings: list[str] = []

    logger.info("运行模式：%s", "打包态" if core.is_frozen() else "开发态")
    logger.info("程序目录：%s", paths.base_dir)
    logger.info("ffmpeg：%s", paths.ffmpeg)
    logger.info("ffprobe：%s", paths.ffprobe)
    logger.info("日志目录：%s", paths.logs_dir)
    logger.info("配置目录：%s", paths.config_dir)
    logger.info("work 目录：%s", paths.work_dir)
    logger.info("Python：%s", sys.version.replace("\n", " "))

    if log_warning:
        warnings.append(log_warning)

    # 启动时清理上次异常退出遗留的 job_*。清理失败只提示，不阻塞启动。
    try:
        cleanup_failures = core.cleanup_stale_jobs(paths.work_dir)
    except Exception as exc:
        logger.warning("扫描残留任务目录失败：%s", exc)
        cleanup_failures = [(paths.work_dir, str(exc))]
    if cleanup_failures:
        lines = ["以下残留临时目录清理失败，请手动删除（不影响程序启动）："]
        for path, reason in cleanup_failures:
            lines.append(f"• {path}\n  {reason}")
        warnings.append("\n".join(lines))
        logger.warning("存在无法清理的残留目录：%s", cleanup_failures)

    config_store = core.ConfigStore(paths.config_dir / "settings.json", logger)
    settings, config_warning = config_store.load()
    if config_warning:
        warnings.append(config_warning)

    root = tk.Tk()
    # 在 GUI 构建前处理配置目录不可写的情况。
    try:
        paths.config_dir.mkdir(parents=True, exist_ok=True)
        probe = paths.config_dir / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        warnings.append(
            "程序目录不可写，配置无法保存到 config/。\n"
            f"路径：{paths.config_dir}\n原因：{exc}\n\n"
            "仍可正常烧录；如需保存设置，请把程序移动到可写目录。"
        )

    try:
        BurnApp(
            root=root,
            paths=paths,
            logger=logger,
            config_store=config_store,
            settings=settings,
            startup_warnings=warnings,
        )
        logger.info("GUI 已启动")
        root.mainloop()
    except Exception:
        logger.exception("GUI 异常退出")
        raise
    finally:
        logger.info("程序退出")


if __name__ == "__main__":
    main()
