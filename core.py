"""字幕烧录工具的核心逻辑。

这个模块刻意不依赖 tkinter，方便单独测试：
- 查找同名字幕、检测字幕编码并转成 UTF-8；
- 用 ffprobe 读取视频信息与 HDR 标记；
- 组装并运行 ffmpeg 命令；
- 管理 work/job_* 临时目录、输出命名与原子落盘。

硬性约束（请勿在未同步更新文档/测试的情况下修改）：
- 不使用 scale 滤镜，不改变分辨率、帧率、SAR/DAR；
- 视频使用 libx264 / preset medium / 可配置 CRF（默认 18）；
- 音频只允许 -c:a copy，绝不静默转码；
- 输出容器跟随输入扩展名，mp4 使用 +faststart；
- 字幕先转 UTF-8，工作目录只放相对文件名，避免 Windows 滤镜路径转义问题。
"""

from __future__ import annotations

import codecs
import json
import logging
import logging.handlers
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

APP_NAME = "字幕烧录工具"
APP_VERSION = "1.0.0"

SUBTITLE_EXTENSIONS = (".srt", ".ass")
DEFAULT_CRF = 18
MIN_CRF = 0
MAX_CRF = 51

DEFAULT_FONT_NAME = "Microsoft YaHei"
DEFAULT_FONT_SIZE = 20
DEFAULT_MARGIN_V = 28
DEFAULT_COLOR_NAME = "白色"

# ASS 颜色格式是 &HAABBGGRR（AA=透明度，00 表示不透明）。
PRIMARY_COLOURS: dict[str, str] = {
    "白色": "&H00FFFFFF",
    "黄色": "&H0000FFFF",
    "浅绿色": "&H0000FF00",
}

_LOG = logging.getLogger("subburn")


class BurnError(Exception):
    """可展示给用户的错误。details 中保留 ffmpeg/ffprobe 原文。"""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message = message
        self.details = details or ""

    def __str__(self) -> str:
        return self.message


class BurnCancelled(Exception):
    """用户主动取消。"""


# ---------------------------------------------------------------------------
# 路径与工具定位
# ---------------------------------------------------------------------------


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def program_dir() -> Path:
    """返回程序根目录。

    打包态：exe 所在目录（sys.executable 的父目录）。
    开发态：本脚本所在目录。
    """

    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path
    logs_dir: Path
    config_dir: Path
    work_dir: Path
    ffmpeg: Path
    ffprobe: Path

    @classmethod
    def build(cls) -> "AppPaths":
        base = program_dir()
        # 开发态和打包态都以程序目录为基准；函数 program_dir() 明确区分了两者。
        return cls(
            base_dir=base,
            logs_dir=base / "logs",
            config_dir=base / "config",
            work_dir=base / "work",
            ffmpeg=base / "ffmpeg.exe",
            ffprobe=base / "ffprobe.exe",
        )


def missing_tools(paths: AppPaths) -> list[Path]:
    result: list[Path] = []
    for tool in (paths.ffmpeg, paths.ffprobe):
        if not tool.is_file():
            result.append(tool)
    return result


def _creationflags_no_window() -> int:
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


# ---------------------------------------------------------------------------
# 字幕匹配与编码
# ---------------------------------------------------------------------------


def find_matching_subtitles(video_path: str | Path) -> list[Path]:
    """查找视频同目录下同主文件名的 .srt/.ass，SRT 优先，其次 ASS。"""

    video = Path(video_path)
    try:
        entries = list(video.parent.iterdir())
    except OSError:
        return []

    stem = video.stem.casefold()
    matches: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if entry.suffix.casefold() not in SUBTITLE_EXTENSIONS:
            continue
        if entry.stem.casefold() != stem:
            continue
        matches.append(entry)

    # Windows 文件系统大小写不敏感，但这里仍显式排序，保证行为稳定。
    matches.sort(key=lambda p: (p.suffix.casefold() != ".srt", p.name.casefold()))
    return matches


# 常用汉字，用于在 GB18030 / BIG5 之间做初步判别。这里不追求语言学完美，
# 只要能把绝大多数中文字幕正确识别即可；识别结果会写入日志。
_COMMON_HANZI = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动"
    "同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自"
    "二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日"
    "那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变"
    "条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员位入常文总次"
    "品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几"
    "九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压"
    "志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场"
    "身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温"
    "传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断"
    "深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值"
    "号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆"
    "包火住调满县局照参红细引听该铁价严龙飞"
)
_CJK_PUNCTUATION = set("，。！？：；“”‘’、—…《》（）【】·")


def _text_plausibility(text: str) -> float:
    """粗略评估解码后文本像不像正常字幕。分数越高越可信。"""

    if not text:
        return -1_000.0

    sample = text[:200_000]
    length = len(sample)
    cjk = 0
    common = 0
    punctuation = 0
    ascii_printable = 0
    controls = 0
    unassigned_or_private = 0
    lowercase_latin_accent = 0

    for ch in sample:
        code = ord(ch)
        if "\u4e00" <= ch <= "\u9fff":
            cjk += 1
            if ch in _COMMON_HANZI:
                common += 1
        if ch in _CJK_PUNCTUATION:
            punctuation += 1
        if ch in "\r\n\t" or 32 <= code < 127:
            ascii_printable += 1
        elif code < 32 or code == 0x7F:
            controls += 1
        # 西文（法语/德语等）字幕常见的小写重音字母。中文编码被误解码成
        # CP1252 时通常是一串大写重音乱码，因此只奖励小写形式。
        if "\u00e0" <= ch <= "\u00ff" and ch.islower():
            lowercase_latin_accent += 1
        cat = unicodedata.category(ch)
        if cat in {"Co", "Cn", "Cs"}:
            unassigned_or_private += 1

    score = (
        common * 3.0
        + cjk * 0.35
        + punctuation * 1.2
        + ascii_printable * 0.03
        + lowercase_latin_accent * 1.0
        - controls * 30.0
        - unassigned_or_private * 12.0
    )
    if " --> " in text or "-->" in text:
        score += 12.0
    return score / max(length, 1)


def detect_subtitle_encoding(raw: bytes) -> str:
    """检测字幕编码，返回 Python codec 名称。

    覆盖 Windows 下常见情况：UTF-8/UTF-8 BOM、UTF-16/32 BOM、无 BOM UTF-16、
    GB18030/GBK、BIG5、Shift_JIS、CP1252。UTF-8 永远优先。
    """

    if not raw:
        raise BurnError("字幕文件为空。")

    if raw.startswith(codecs.BOM_UTF32_LE):
        return "utf-32-le"
    if raw.startswith(codecs.BOM_UTF32_BE):
        return "utf-32-be"
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"

    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # 无 BOM 的 UTF-16 常见于 Windows 记事本旧版本保存的文件。
    if len(raw) >= 4:
        sample = raw[: min(len(raw), 8192)]
        null_ratio = sample.count(0) / len(sample)
        if null_ratio > 0.10:
            even_null = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
            odd_null = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
            codec = "utf-16-be" if even_null > odd_null else "utf-16-le"
            try:
                raw.decode(codec)
                return codec
            except UnicodeDecodeError:
                pass

    candidates: list[tuple[float, int, str, str]] = []
    # 数值越小优先级越高。中文 Windows 字幕以 GB18030 最常见，BIG5 次之。
    for priority, codec in enumerate(("gb18030", "big5", "shift_jis", "cp1252")):
        try:
            text = raw.decode(codec, errors="strict")
        except UnicodeDecodeError:
            continue
        candidates.append((_text_plausibility(text), -priority, codec, text))

    if not candidates:
        raise BurnError(
            "无法识别字幕文件编码。",
            "请用记事本或其他编辑器把字幕另存为 UTF-8 后再试。",
        )

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


def decode_subtitle_file(subtitle_path: str | Path) -> tuple[str, str]:
    """读取字幕并返回 (UTF-8 文本, 原编码名称)。"""

    path = Path(subtitle_path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise BurnError(f"字幕文件不存在：\n{path}") from exc
    except OSError as exc:
        raise BurnError(f"无法读取字幕文件：\n{path}", str(exc)) from exc

    encoding = detect_subtitle_encoding(raw)
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise BurnError(
            "字幕文件编码转换失败。",
            f"检测到的编码：{encoding}\n{exc}",
        ) from exc

    text = _strip_bom(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    if not text.strip():
        raise BurnError("字幕文件没有可用文本（内容为空）。")
    return text, encoding


# ---------------------------------------------------------------------------
# ffprobe 与视频信息
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float | None
    duration: float | None
    video_codec: str
    audio_codecs: tuple[str, ...]
    pix_fmt: str
    container: str
    hdr: bool
    hdr_reasons: tuple[str, ...]
    rotation: int = 0

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def fps_text(self) -> str:
        if not self.fps:
            return "未知"
        if abs(self.fps - round(self.fps)) < 0.005:
            return f"{self.fps:.0f} fps"
        return f"{self.fps:.3f}".rstrip("0").rstrip(".") + " fps"

    @property
    def duration_text(self) -> str:
        return format_clock(self.duration)

    @property
    def audio_text(self) -> str:
        return ", ".join(self.audio_codecs) if self.audio_codecs else "无音轨"

    def summary_text(self) -> str:
        return (
            f"分辨率：{self.resolution}    帧率：{self.fps_text}    时长：{self.duration_text}\n"
            f"视频编码：{self.video_codec} / {self.pix_fmt or '未知像素格式'}    "
            f"音频：{self.audio_text}    容器：{self.container or '未知'}"
        )

    def hdr_text(self) -> str:
        return "、".join(self.hdr_reasons)


def format_clock(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "未知"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_fraction(value: object) -> float | None:
    if not value or not isinstance(value, str):
        return None
    if "/" in value:
        num_s, den_s = value.split("/", 1)
        try:
            num = float(num_s)
            den = float(den_s)
        except ValueError:
            return None
        if den <= 0 or num <= 0:
            return None
        fps = num / den
    else:
        try:
            fps = float(value)
        except ValueError:
            return None
    if not math.isfinite(fps) or fps <= 0 or fps > 1000:
        return None
    return fps


def _parse_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _hdr_reasons(video_stream: dict, format_info: dict) -> list[str]:
    reasons: list[str] = []

    transfer = str(video_stream.get("color_transfer", "") or "").casefold()
    primaries = str(video_stream.get("color_primaries", "") or "").casefold()
    space = str(video_stream.get("color_space", "") or "").casefold()

    if transfer == "smpte2084":
        reasons.append("HDR10（PQ / smpte2084）")
    elif transfer == "arib-std-b67":
        reasons.append("HLG（arib-std-b67）")
    elif transfer and transfer not in {"bt709", "unknown", "reserved"}:
        reasons.append(f"非 SDR 传输特性（{transfer}）")

    if primaries == "bt2020":
        reasons.append("BT.2020 广色域")
    if space in {"bt2020nc", "bt2020c"} and "BT.2020 广色域" not in reasons:
        reasons.append(f"BT.2020 色域（{space}）")

    side_data_list = video_stream.get("side_data_list") or []
    for side_data in side_data_list:
        if not isinstance(side_data, dict):
            continue
        kind = str(side_data.get("side_data_type", "") or "")
        kind_cf = kind.casefold()
        if "dovi" in kind_cf or "dolby vision" in kind_cf:
            reasons.append("Dolby Vision 动态元数据")
        if "mastering display" in kind_cf or "content light" in kind_cf:
            reasons.append("HDR 母版/亮度元数据")

    # 部分封装会把 HDR 标记放在 format tags 中。
    tags = format_info.get("tags") or {}
    if isinstance(tags, dict):
        combined = " ".join(str(v) for v in tags.values()).casefold()
        if "smpte2084" in combined and not any("HDR10" in r for r in reasons):
            reasons.append("HDR10（smpte2084）")
        if "arib-std-b67" in combined and not any("HLG" in r for r in reasons):
            reasons.append("HLG（arib-std-b67）")

    # 去重并保持顺序
    result: list[str] = []
    for reason in reasons:
        if reason not in result:
            result.append(reason)
    return result


def parse_probe_json(data: dict, source_path: str = "") -> VideoInfo:
    streams = data.get("streams") or []
    format_info = data.get("format") or {}
    if not isinstance(streams, list) or not streams:
        raise BurnError(
            "无法读取视频流信息。",
            f"ffprobe 未返回视频流。\n文件：{source_path}" if source_path else "ffprobe 未返回视频流。",
        )

    video_stream = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise BurnError(
            "所选文件不包含视频流，无法烧录字幕。",
            f"文件：{source_path}" if source_path else "",
        )

    try:
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width <= 0 or height <= 0:
        raise BurnError("无法读取视频分辨率。", f"文件：{source_path}" if source_path else "")

    fps = _parse_fraction(video_stream.get("avg_frame_rate"))
    if fps is None:
        fps = _parse_fraction(video_stream.get("r_frame_rate"))

    duration = _parse_float(format_info.get("duration"))
    if duration is None:
        duration = _parse_float(video_stream.get("duration"))

    audio_codecs: list[str] = []
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "audio":
            codec = str(stream.get("codec_name") or "未知音频编码")
            if codec not in audio_codecs:
                audio_codecs.append(codec)

    rotation = 0
    for side_data in video_stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            try:
                rotation = int(side_data.get("rotation") or 0)
            except (TypeError, ValueError):
                pass
    if not rotation:
        tags = video_stream.get("tags") or {}
        if isinstance(tags, dict):
            try:
                rotation = int(tags.get("rotate") or 0)
            except (TypeError, ValueError):
                pass

    reasons = _hdr_reasons(video_stream, format_info)
    return VideoInfo(
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        video_codec=str(video_stream.get("codec_name") or "未知"),
        audio_codecs=tuple(audio_codecs),
        pix_fmt=str(video_stream.get("pix_fmt") or ""),
        container=str(format_info.get("format_long_name") or format_info.get("format_name") or ""),
        hdr=bool(reasons),
        hdr_reasons=tuple(reasons),
        rotation=rotation,
    )


def probe_video(ffprobe_path: str | Path, video_path: str | Path) -> VideoInfo:
    ffprobe = Path(ffprobe_path)
    video = Path(video_path)
    if not ffprobe.is_file():
        raise BurnError(
            "未找到 ffprobe.exe。",
            f"请确认 ffprobe.exe 与此程序位于同一目录：\n{ffprobe.parent}",
        )
    if not video.is_file():
        raise BurnError(f"视频文件不存在：\n{video}")

    cmd = [
        str(ffprobe),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video),
    ]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=_creationflags_no_window(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BurnError(
            "读取视频信息超时（ffprobe 超过 60 秒）。",
            "请检查视频文件是否损坏，或文件是否位于响应很慢的网络位置。",
        ) from exc
    except FileNotFoundError as exc:
        raise BurnError("无法启动 ffprobe.exe。", str(exc)) from exc
    except OSError as exc:
        raise BurnError("无法启动 ffprobe.exe。", str(exc)) from exc

    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0 or not completed.stdout.strip():
        raise BurnError(
            "无法读取视频信息（ffprobe 执行失败）。",
            stderr or "ffprobe 没有返回任何数据。",
        )

    try:
        data = json.loads(completed.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise BurnError(
            "ffprobe 返回的视频信息无法解析。",
            f"{exc}\n\nffprobe 输出：\n{completed.stdout[:2000]}",
        ) from exc

    return parse_probe_json(data, str(video))


# ---------------------------------------------------------------------------
# 字幕滤镜与 ffmpeg 命令
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubtitleStyle:
    font_name: str = DEFAULT_FONT_NAME
    font_size: int = DEFAULT_FONT_SIZE
    margin_v: int = DEFAULT_MARGIN_V
    primary_colour: str = PRIMARY_COLOURS[DEFAULT_COLOR_NAME]

    def normalized(self) -> "SubtitleStyle":
        font = re.sub(r"[,:'\\;\[\]{}]", " ", self.font_name or "").strip()
        # 滤镜参数保持纯 ASCII；中文字体请用其英文注册名（Windows 自带
        # Microsoft YaHei / SimHei / SimSun 等均有英文名）。
        font = "".join(ch for ch in font if 32 <= ord(ch) < 127)
        font = re.sub(r"\s+", " ", font).strip() or DEFAULT_FONT_NAME
        size = min(max(_safe_int(self.font_size, DEFAULT_FONT_SIZE), 6), 120)
        margin = min(max(_safe_int(self.margin_v, DEFAULT_MARGIN_V), 0), 500)
        colour = self.primary_colour
        if not re.fullmatch(r"&H[0-9A-Fa-f]{8}", colour or ""):
            colour = PRIMARY_COLOURS[DEFAULT_COLOR_NAME]
        return SubtitleStyle(font, size, margin, colour.upper())


@dataclass(frozen=True)
class SubtitleCopy:
    filter_filename: str
    suffix: str
    encoding: str
    source_path: Path
    temp_path: Path


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def build_subtitle_filter(
    filter_filename: str,
    subtitle_suffix: str,
    style: SubtitleStyle,
) -> str:
    """生成滤镜串。

    filter_filename 必须只是工作目录中的纯 ASCII 文件名（sub.srt/sub.ass）。
    滤镜顺序严格为 subtitles 在前、pad 在后。
    """

    if filter_filename not in {"sub.srt", "sub.ass"}:
        raise ValueError("内部错误：字幕滤镜文件名必须是 sub.srt 或 sub.ass。")
    if subtitle_suffix.casefold() not in SUBTITLE_EXTENSIONS:
        raise ValueError("内部错误：不支持的字幕扩展名。")

    subtitle_filter = f"subtitles={filter_filename}"
    if subtitle_suffix.casefold() == ".srt":
        normalized = style.normalized()
        # Windows 下 libass 会从系统字体中查找 Microsoft YaHei。整段 force_style
        # 用单引号包住，使逗号不会被误认为滤镜分隔符。
        force_style = ",".join(
            [
                f"FontName={normalized.font_name}",
                f"FontSize={normalized.font_size}",
                f"PrimaryColour={normalized.primary_colour}",
                "OutlineColour=&H00000000",
                "Outline=1",
                "Shadow=0",
                "Alignment=2",
                f"MarginV={normalized.margin_v}",
            ]
        )
        subtitle_filter += f":force_style='{force_style}'"
    # ASS：不添加 force_style，完整保留字幕自带样式。libass 在字体缺失时会
    # 自动使用系统字体回退，不做强制覆盖。

    return f"{subtitle_filter},pad=ceil(iw/2)*2:ceil(ih/2)*2"


def write_utf8_subtitle(source_path: str | Path, job_dir: str | Path) -> SubtitleCopy:
    source = Path(source_path)
    suffix = source.suffix.casefold()
    if suffix not in SUBTITLE_EXTENSIONS:
        raise BurnError(
            "字幕格式不支持。",
            f"只支持 .srt 和 .ass，当前文件：{source.name}",
        )
    if not source.is_file():
        raise BurnError(f"字幕文件不存在：\n{source}")

    text, encoding = decode_subtitle_file(source)
    # 只保留原扩展名，文件名固定为 sub.srt / sub.ass，保证滤镜参数纯 ASCII。
    filter_filename = f"sub{suffix}"
    dest = Path(job_dir) / filter_filename
    try:
        with open(dest, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        raise BurnError("无法把字幕写入临时目录。", f"{dest}\n{exc}") from exc

    _LOG.info("字幕已转为 UTF-8：%s（原编码 %s） -> %s", source, encoding, dest)
    return SubtitleCopy(
        filter_filename=filter_filename,
        suffix=suffix,
        encoding=encoding,
        source_path=source,
        temp_path=dest,
    )


def build_ffmpeg_command(
    ffmpeg_path: str | Path,
    input_path: str | Path,
    output_path: str | Path,
    subtitle_filter: str,
    video_info: VideoInfo,
    crf: int,
) -> list[str]:
    """组装 ffmpeg 参数。输出容器严格跟随输入扩展名。"""

    if not MIN_CRF <= int(crf) <= MAX_CRF:
        raise BurnError(f"CRF 必须在 {MIN_CRF} 到 {MAX_CRF} 之间。")
    if "scale" in subtitle_filter.casefold():
        # 防御性检查：任何情况下都不应出现 scale。
        raise BurnError("内部错误：滤镜中出现了禁止的 scale。")

    output = Path(output_path)
    suffix = Path(input_path).suffix.casefold()
    cmd: list[str] = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "info",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(Path(input_path).resolve()),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-dn",
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(int(crf)),
        "-c:a",
        "copy",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        # passthrough：不重采样、不改变时间基。不要改成任何 fps 数值。
        # 注意：FFmpeg 8 已移除旧的 -vsync，使用 -fps_mode。
        "-fps_mode",
        "passthrough",
    ]
    if suffix == ".mp4":
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output.resolve()))
    return cmd


# ---------------------------------------------------------------------------
# 临时目录
# ---------------------------------------------------------------------------


def _probe_directory_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise OSError(f"不是目录：{directory}")
    probe = directory / f".write_test_{secrets.token_hex(4)}"
    try:
        with open(probe, "w", encoding="ascii") as handle:
            handle.write("ok")
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_work_root(base_dir: str | Path, output_dir: str | Path) -> tuple[Path, str]:
    """返回可写的 work 根目录，并说明是否降级。

    优先程序目录/work；不可写则使用用户输出目录/.work。两者都不可写时抛出
    明确错误，提示用户把程序移动到可写目录。
    """

    base = Path(base_dir)
    output = Path(output_dir)
    first = base / "work"
    first_error = ""
    try:
        _probe_directory_writable(first)
        return first, ""
    except OSError as exc:
        first_error = f"{first}: {exc}"

    second = output / ".work"
    try:
        _probe_directory_writable(second)
        return second, first_error
    except OSError as exc:
        message = (
            "程序目录和输出目录都不可写，无法创建临时工作区。\n"
            "请把整个程序文件夹移动到可写目录（例如 D:\\工具\\字幕烧录工具）后重试。"
        )
        details = f"程序目录/work：{first_error}\n输出目录/.work：{second}: {exc}"
        raise BurnError(message, details) from exc


def create_job_dir(work_root: str | Path) -> Path:
    root = Path(work_root)
    for _ in range(30):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"job_{stamp}_{secrets.token_hex(3)}"
        path = root / name
        try:
            path.mkdir(parents=False, exist_ok=False)
            return path
        except FileExistsError:
            continue
        except OSError as exc:
            raise BurnError(
                "无法创建临时任务目录。",
                f"{path}\n{exc}",
            ) from exc
    raise BurnError("无法创建唯一的临时任务目录。", f"目录：{root}")


def cleanup_stale_jobs(work_root: str | Path) -> list[tuple[Path, str]]:
    """删除 work/job_* 残留目录，返回删除失败的 (路径, 原因)。"""

    root = Path(work_root)
    failures: list[tuple[Path, str]] = []
    if not root.is_dir():
        return failures
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        return [(root, str(exc))]

    for entry in entries:
        if not entry.name.startswith("job_"):
            continue
        if not entry.is_dir() or entry.is_symlink():
            continue
        try:
            shutil.rmtree(entry)
            _LOG.info("已清理残留任务目录：%s", entry)
        except OSError as exc:
            _LOG.warning("清理残留任务目录失败：%s（%s）", entry, exc)
            failures.append((entry, str(exc)))
    return failures


def _cleanup_job_dir(job_dir: Path, warning_callback: Callable[[str], None] | None) -> None:
    try:
        shutil.rmtree(job_dir)
        _LOG.info("已删除临时任务目录：%s", job_dir)
    except OSError as exc:
        _LOG.warning("删除临时任务目录失败：%s（%s）", job_dir, exc)
        if warning_callback:
            warning_callback(
                f"临时目录清理失败，请手动删除：\n{job_dir}\n\n原因：{exc}"
            )


# ---------------------------------------------------------------------------
# 输出文件规则
# ---------------------------------------------------------------------------


def default_output_path(video_path: str | Path, output_dir: str | Path) -> Path:
    video = Path(video_path)
    return Path(output_dir) / f"{video.stem}_硬字幕{video.suffix}"


def numbered_output_path(
    video_path: str | Path,
    output_dir: str | Path,
    index: int,
) -> Path:
    video = Path(video_path)
    suffix_text = f"_{index}" if index > 0 else ""
    return Path(output_dir) / f"{video.stem}_硬字幕{suffix_text}{video.suffix}"


def same_path(left: str | Path, right: str | Path) -> bool:
    try:
        a = os.path.normcase(os.path.abspath(os.fspath(left)))
        b = os.path.normcase(os.path.abspath(os.fspath(right)))
    except (OSError, ValueError):
        return False
    return a == b


def choose_output_path(
    video_path: str | Path,
    output_dir: str | Path,
    overwrite: bool,
) -> Path:
    """按规则得到输出路径。overwrite=True 时使用默认名，否则追加 _1、_2。"""

    video = Path(video_path)
    out_dir = Path(output_dir)
    candidate = default_output_path(video, out_dir)
    if same_path(candidate, video):
        candidate = numbered_output_path(video, out_dir, 1)
    if overwrite:
        return candidate

    if not candidate.exists():
        return candidate
    for index in range(1, 10_000):
        candidate = numbered_output_path(video, out_dir, index)
        if same_path(candidate, video):
            continue
        if not candidate.exists():
            return candidate
    raise BurnError("无法生成不重名的输出文件名（已尝试 9999 个序号）。")


# ---------------------------------------------------------------------------
# 运行任务
# ---------------------------------------------------------------------------


@dataclass
class BurnResult:
    output_path: Path
    elapsed_seconds: float
    video_info: VideoInfo
    command: tuple[str, ...] = ()


@dataclass
class BurnJob:
    ffmpeg: Path
    ffprobe: Path
    video_path: Path
    subtitle_path: Path
    output_path: Path
    base_dir: Path
    video_info: VideoInfo
    crf: int = DEFAULT_CRF
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    overwrite: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    logger: logging.Logger = field(default=_LOG, repr=False)
    progress_callback: Callable[[float, float | None], None] | None = field(
        default=None, repr=False
    )
    status_callback: Callable[[str], None] | None = field(default=None, repr=False)
    warning_callback: Callable[[str], None] | None = field(default=None, repr=False)


def _status(job: BurnJob, text: str) -> None:
    job.logger.info("状态：%s", text)
    if job.status_callback:
        job.status_callback(text)


def _parse_progress_line(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, value = line.strip().split("=", 1)
    return key, value


def _seconds_from_progress(key: str, value: str) -> float | None:
    try:
        if key == "out_time_us":
            return int(value) / 1_000_000
        if key == "out_time_ms":
            # 旧版 ffmpeg 的 out_time_ms 实际上也以微秒计。
            return int(value) / 1_000_000
        if key == "out_time":
            match = re.match(r"^(-?\d+):(\d+):(\d+(?:\.\d+)?)$", value)
            if not match:
                return None
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None
    return None


def _terminate_process(proc: subprocess.Popen[str], logger: logging.Logger) -> None:
    if proc.poll() is not None:
        return
    logger.info("正在终止 ffmpeg 进程（PID %s）", proc.pid)
    try:
        proc.terminate()
    except OSError as exc:
        logger.warning("终止 ffmpeg 失败：%s", exc)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg 未在 8 秒内退出，强制 kill")
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg 仍未退出（PID %s）", proc.pid)


def _ffmpeg_error_message(raw_stderr: str, video_info: VideoInfo) -> str:
    lower = raw_stderr.casefold()
    audio_error_patterns = (
        "codec not currently supported in container",
        "could not find tag for codec",
        "unsupported codec in container",
        "not currently supported in container",
    )
    if video_info.audio_codecs and any(p in lower for p in audio_error_patterns):
        return (
            "当前容器不支持该音频编码，无法无损复制音频。\n\n"
            "本工具坚持音频不重编码，因此不会自动转成其他音频格式。"
            "请换用支持该音频编码的容器（例如 MKV）后重试。"
        )
    if "unknown encoder 'libx264'" in lower or (
        "encoder 'libx264'" in lower and "not found" in lower
    ):
        return "当前 ffmpeg 不支持 libx264 编码器。请换用带 libx264 的 static 版本 ffmpeg。"
    if "no such filter" in lower and ("subtitles" in lower or "ass" in lower):
        return "当前 ffmpeg 缺少 libass 字幕滤镜，无法烧录字幕。请换用完整 static 构建。"
    if "fps_mode" in lower and (
        "unrecognized option" in lower or "option not found" in lower
    ):
        return "当前 ffmpeg 版本过旧，不支持 -fps_mode。请换用 FFmpeg 5.1+ 的 static 构建。"
    if "error opening input" in lower or "no such file or directory" in lower:
        return "无法打开输入文件。请确认视频路径存在且可访问。"
    return "视频编码失败。请查看下方 ffmpeg 报错原文，或打开日志查看完整记录。"


def _run_ffmpeg(
    job: BurnJob,
    cmd: Sequence[str],
    cwd: Path,
    duration: float | None,
) -> str:
    """运行 ffmpeg，实时上报进度，返回 stderr 文本。"""

    env = dict(os.environ)
    env.setdefault("LC_ALL", "C")
    env.setdefault("LANG", "C")
    job.logger.info("启动 ffmpeg，工作目录：%s", cwd)
    job.logger.info("ffmpeg 命令：%s", subprocess.list2cmdline(list(cmd)))

    try:
        proc = subprocess.Popen(
            list(cmd),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_creationflags_no_window(),
            env=env,
        )
    except FileNotFoundError as exc:
        raise BurnError(
            "未找到 ffmpeg.exe。",
            f"请确认 ffmpeg.exe 与此程序位于同一目录：\n{job.ffmpeg.parent}",
        ) from exc
    except OSError as exc:
        raise BurnError("无法启动 ffmpeg.exe。", str(exc)) from exc

    stderr_lines: deque[str] = deque(maxlen=600)
    stdout_lines: deque[str] = deque()
    lock = threading.Lock()

    def read_stdout() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                with lock:
                    stdout_lines.append(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    def read_stderr() -> None:
        assert proc.stderr is not None
        try:
            for line in proc.stderr:
                stripped = line.rstrip("\r\n")
                with lock:
                    stderr_lines.append(stripped)
                job.logger.debug("ffmpeg: %s", stripped)
        except (OSError, ValueError):
            pass

    stdout_thread = threading.Thread(target=read_stdout, name="ffmpeg-stdout", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name="ffmpeg-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    last_progress = 0.0
    cancelled = False
    try:
        while proc.poll() is None:
            if job.cancel_event.is_set():
                cancelled = True
                _terminate_process(proc, job.logger)
                break

            with lock:
                lines = list(stdout_lines)
                stdout_lines.clear()
            for line in lines:
                parsed = _parse_progress_line(line)
                if not parsed:
                    continue
                key, value = parsed
                seconds = _seconds_from_progress(key, value)
                if seconds is not None and job.progress_callback:
                    now = time.monotonic()
                    if now - last_progress >= 0.2:
                        last_progress = now
                        try:
                            job.progress_callback(seconds, duration)
                        except Exception:
                            job.logger.debug("进度回调异常", exc_info=True)
            time.sleep(0.08)

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _terminate_process(proc, job.logger)
            proc.wait(timeout=5)

        stdout_thread.join(timeout=3)
        stderr_thread.join(timeout=3)
        with lock:
            remaining_stdout = list(stdout_lines)
            stderr_text = "\n".join(stderr_lines)
        for line in remaining_stdout:
            parsed = _parse_progress_line(line)
            if parsed:
                seconds = _seconds_from_progress(*parsed)
                if seconds is not None and job.progress_callback:
                    try:
                        job.progress_callback(seconds, duration)
                    except Exception:
                        pass
    finally:
        if proc.poll() is None:
            _terminate_process(proc, job.logger)
        # 显式关闭管道，避免 Windows 上长时间运行时累积文件句柄。
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    if cancelled or job.cancel_event.is_set():
        raise BurnCancelled()
    if proc.returncode != 0:
        raise BurnError(_ffmpeg_error_message(stderr_text, job.video_info), stderr_text)
    return stderr_text


def _validate_job(job: BurnJob) -> tuple[Path, Path, Path]:
    video = Path(job.video_path)
    subtitle = Path(job.subtitle_path)
    output = Path(job.output_path)

    if not video.is_file():
        raise BurnError(f"视频文件不存在：\n{video}")
    if not subtitle.is_file():
        raise BurnError(f"字幕文件不存在：\n{subtitle}")
    if subtitle.suffix.casefold() not in SUBTITLE_EXTENSIONS:
        raise BurnError("字幕格式不支持。", f"只支持 .srt 和 .ass：\n{subtitle}")
    if not Path(job.ffmpeg).is_file():
        raise BurnError(
            "未找到 ffmpeg.exe。",
            f"请确认 ffmpeg.exe 与此程序位于同一目录：\n{Path(job.ffmpeg).parent}",
        )
    if not video.suffix:
        raise BurnError("无法确定输出容器。", f"视频文件没有扩展名：\n{video}")
    if same_path(output, video):
        raise BurnError("输出路径与输入原视频相同，已阻止覆盖原视频。")
    if output.exists() and not job.overwrite:
        raise BurnError("输出文件已存在。", f"为避免意外覆盖，本次任务已停止：\n{output}")
    if not output.parent.is_dir():
        raise BurnError("输出目录不存在。", f"{output.parent}")
    return video.resolve(), subtitle.resolve(), output.resolve()


def run_burn_job(job: BurnJob) -> BurnResult:
    """执行一次完整烧录。成功返回 BurnResult，失败抛 BurnError，取消抛 BurnCancelled。

    无论成功、失败还是取消，job 子目录都会在 finally 中删除；临时视频文件位于
    输出目录（同盘原子替换），也会在失败/取消时删除。
    """

    started = time.monotonic()
    job_dir: Path | None = None
    temp_output: Path | None = None
    moved = False
    logger = job.logger

    try:
        video, subtitle, output = _validate_job(job)
        job.logger.info(
            "开始任务：video=%s | subtitle=%s | output=%s", video, subtitle, output
        )
        _status(job, "正在准备临时工作区…")

        work_root, fallback_reason = ensure_work_root(job.base_dir, output.parent)
        if fallback_reason:
            job.logger.warning("程序目录 work 不可写，已降级：%s", fallback_reason)
            if job.warning_callback:
                job.warning_callback(
                    "程序所在目录不可写，临时字幕将放在输出目录下的 .work/。\n"
                    "日志和配置可能也无法写入程序目录。"
                )
        job.logger.info("本次任务 work 根目录：%s", work_root)

        job_dir = create_job_dir(work_root)
        job.logger.info("本次任务临时目录：%s", job_dir)
        if job.cancel_event.is_set():
            raise BurnCancelled()

        _status(job, "正在检测字幕编码并转为 UTF-8…")
        subtitle_copy = write_utf8_subtitle(subtitle, job_dir)
        job.logger.info("本次任务字幕临时文件：%s", subtitle_copy.temp_path)

        if job.cancel_event.is_set():
            raise BurnCancelled()

        subtitle_filter = build_subtitle_filter(
            subtitle_copy.filter_filename,
            subtitle_copy.suffix,
            job.style,
        )
        job.logger.info("字幕滤镜（相对路径）：%s", subtitle_filter)

        # 临时视频与最终文件位于同一目录，成功后用 os.replace 原子替换。
        temp_output = output.parent / (
            f".{output.stem}.{job_dir.name}.part{output.suffix}"
        )
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError as exc:
                raise BurnError("无法删除旧的临时视频文件。", f"{temp_output}\n{exc}") from exc
        job.logger.info("临时输出视频路径：%s", temp_output)
        job.logger.info("最终输出视频路径：%s", output)

        cmd = build_ffmpeg_command(
            job.ffmpeg,
            video,
            temp_output,
            subtitle_filter,
            job.video_info,
            job.crf,
        )
        _status(job, "正在编码，请稍候…")
        raw_stderr = _run_ffmpeg(job, cmd, job_dir, job.video_info.duration)

        if job.cancel_event.is_set():
            raise BurnCancelled()
        if not temp_output.is_file() or temp_output.stat().st_size <= 0:
            raise BurnError("ffmpeg 未生成有效的输出视频。", raw_stderr)
        if output.exists() and not job.overwrite:
            raise BurnError(
                "输出文件在任务进行期间已存在，已停止以免覆盖。",
                f"{output}\n请重试并确认覆盖，或换一个输出目录。",
            )

        try:
            os.replace(temp_output, output)
            moved = True
        except OSError as exc:
            raise BurnError(
                "无法把编码结果移动到最终输出路径。",
                f"{temp_output} -> {output}\n{exc}",
            ) from exc

        elapsed = time.monotonic() - started
        if job.progress_callback:
            try:
                job.progress_callback(job.video_info.duration or 0, job.video_info.duration)
            except Exception:
                pass
        _status(job, "烧录完成")
        job.logger.info("烧录成功：%s（耗时 %.1f 秒）", output, elapsed)
        return BurnResult(
            output_path=output,
            elapsed_seconds=elapsed,
            video_info=job.video_info,
            command=tuple(cmd),
        )

    except BurnCancelled:
        job.logger.info("任务已取消")
        raise
    except BurnError as exc:
        job.logger.error("任务失败：%s", exc.message)
        if exc.details:
            job.logger.error("错误详情：%s", exc.details)
        raise
    except Exception as exc:
        job.logger.exception("任务出现未预期异常")
        raise BurnError(
            "发生了未预期的错误。",
            f"{exc}\n\n{traceback.format_exc()}",
        ) from exc
    finally:
        if temp_output is not None and not moved:
            try:
                temp_output.unlink(missing_ok=True)
                if temp_output.exists():
                    raise OSError("文件仍存在")
                job.logger.info("已删除临时输出视频：%s", temp_output)
            except OSError as exc:
                job.logger.warning("删除临时输出视频失败：%s（%s）", temp_output, exc)
                if job.warning_callback:
                    job.warning_callback(
                        f"临时视频文件清理失败，请手动删除：\n{temp_output}\n\n原因：{exc}"
                    )
        if job_dir is not None:
            _cleanup_job_dir(job_dir, job.warning_callback)


# ---------------------------------------------------------------------------
# 日志与配置
# ---------------------------------------------------------------------------


def setup_logging(paths: AppPaths) -> tuple[logging.Logger, str]:
    """日志写入程序目录/logs。失败时返回警告，不写入任何 AppData。"""

    logger = logging.getLogger("subburn")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    warning = ""
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = paths.logs_dir / "app.log"
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(threadName)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.info("=" * 70)
        logger.info("%s v%s 启动", APP_NAME, APP_VERSION)
    except OSError as exc:
        warning = (
            f"无法在程序目录创建日志文件，日志功能不可用。\n"
            f"路径：{paths.logs_dir}\n原因：{exc}\n\n"
            "程序仍可继续使用；如需完整日志，请把程序移动到可写目录。"
        )
        logger.addHandler(logging.NullHandler())  # type: ignore[arg-type]
    return logger, warning


@dataclass
class ConfigStore:
    path: Path
    logger: logging.Logger = field(default=_LOG, repr=False)

    def load(self) -> tuple[dict, str]:
        if not self.path.exists():
            return {}, ""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("配置根节点不是对象")
            return data, ""
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.logger.warning("读取配置失败：%s（%s）", self.path, exc)
            return {}, f"配置文件读取失败，已使用默认设置。\n{self.path}\n{exc}"

    def save(self, settings: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(settings, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_path, self.path)
            self.logger.info("配置已保存：%s", self.path)
        except (OSError, TypeError, ValueError) as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.logger.warning("保存配置失败：%s（%s）", self.path, exc)
            raise
