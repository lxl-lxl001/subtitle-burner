from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402


CHINESE = "这是第一条中文字幕。\nThis is a subtitle.\n"


@contextmanager
def workspace_tmp():
    """在项目目录内创建测试目录。

    不使用系统 %TEMP%：此工作区的文件沙箱对 tempfile 创建的 0700 目录有限制。
    生产代码同样禁止使用系统 %TEMP%，这里是测试专用辅助函数。
    """

    base = ROOT / "tests" / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    folder = base / f"case_{uuid.uuid4().hex}"
    folder.mkdir()
    try:
        yield str(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


class SubtitleMatchingTests(unittest.TestCase):
    def test_auto_match_prefers_srt_and_keeps_ass_candidate(self) -> None:
        with workspace_tmp() as temp:
            folder = Path(temp)
            video = folder / "movie.mp4"
            video.write_bytes(b"x")
            ass = folder / "movie.ass"
            srt = folder / "movie.srt"
            ass.write_text("ass", encoding="utf-8")
            srt.write_text("srt", encoding="utf-8")
            (folder / "movie.zh.srt").write_text("different stem", encoding="utf-8")
            (folder / "other.srt").write_text("other", encoding="utf-8")

            result = core.find_matching_subtitles(video)
            self.assertEqual([p.name for p in result], ["movie.srt", "movie.ass"])

    def test_case_insensitive_extension_and_stem(self) -> None:
        with workspace_tmp() as temp:
            folder = Path(temp)
            video = folder / "Movie.MKV"
            video.write_bytes(b"x")
            upper = folder / "movie.SRT"
            upper.write_text("srt", encoding="utf-8")
            result = core.find_matching_subtitles(video)
            self.assertEqual(result, [upper])


class EncodingTests(unittest.TestCase):
    def test_detect_utf8_bom(self) -> None:
        raw = b"\xef\xbb\xbf" + CHINESE.encode("utf-8")
        self.assertEqual(core.detect_subtitle_encoding(raw), "utf-8-sig")

    def test_detect_utf16_bom(self) -> None:
        raw = b"\xff\xfe" + CHINESE.encode("utf-16-le")
        self.assertEqual(core.detect_subtitle_encoding(raw), "utf-16-le")

    def test_detect_gb18030(self) -> None:
        raw = CHINESE.encode("gb18030")
        self.assertEqual(core.detect_subtitle_encoding(raw), "gb18030")

    def test_detect_big5(self) -> None:
        text = "這是一條中文字幕測試。\n第二行字幕內容。\n"
        self.assertEqual(core.detect_subtitle_encoding(text.encode("big5")), "big5")

    def test_detect_western_cp1252(self) -> None:
        text = "Bonjour, ceci est un sous-titre.\nDeuxième ligne avec accents.\n"
        self.assertEqual(core.detect_subtitle_encoding(text.encode("cp1252")), "cp1252")

    def test_write_utf8_keeps_extension(self) -> None:
        with workspace_tmp() as temp:
            root = Path(temp)
            source = root / "movie.ASS"
            source.write_bytes(CHINESE.encode("gb18030"))
            job = root / "job"
            job.mkdir()
            copied = core.write_utf8_subtitle(source, job)
            self.assertEqual(copied.filter_filename, "sub.ass")
            self.assertEqual(copied.temp_path.name, "sub.ass")
            self.assertEqual(copied.temp_path.read_text(encoding="utf-8").strip(), CHINESE.strip())

    def test_empty_subtitle_rejected(self) -> None:
        with workspace_tmp() as temp:
            source = Path(temp) / "empty.srt"
            source.write_bytes(b"")
            with self.assertRaises(core.BurnError):
                core.decode_subtitle_file(source)


class ProbeParsingTests(unittest.TestCase):
    def _data(self) -> dict:
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "avg_frame_rate": "24000/1001",
                    "color_transfer": "smpte2084",
                    "color_primaries": "bt2020",
                    "color_space": "bt2020nc",
                    "side_data_list": [
                        {"side_data_type": "Dolby Vision Metadata"},
                        {"side_data_type": "Mastering display metadata"},
                    ],
                },
                {"codec_type": "audio", "codec_name": "truehd"},
                {"codec_type": "audio", "codec_name": "ac3"},
            ],
            "format": {"duration": "123.456", "format_long_name": "Matroska / WebM"},
        }

    def test_parse_hdr_and_audio(self) -> None:
        info = core.parse_probe_json(self._data(), "movie.mkv")
        self.assertEqual(info.width, 3840)
        self.assertEqual(info.height, 2160)
        self.assertAlmostEqual(info.fps or 0, 24000 / 1001, places=4)
        self.assertEqual(info.audio_codecs, ("truehd", "ac3"))
        self.assertTrue(info.hdr)
        joined = info.hdr_text()
        self.assertIn("HDR10", joined)
        self.assertIn("Dolby Vision", joined)
        self.assertIn("BT.2020", joined)

    def test_sdr_is_not_flagged(self) -> None:
        data = self._data()
        stream = data["streams"][0]
        stream["color_transfer"] = "bt709"
        stream["color_primaries"] = "bt709"
        stream["color_space"] = "bt709"
        stream["side_data_list"] = []
        info = core.parse_probe_json(data)
        self.assertFalse(info.hdr)

    def test_no_video_stream(self) -> None:
        with self.assertRaises(core.BurnError):
            core.parse_probe_json({"streams": [{"codec_type": "audio"}], "format": {}})


class FilterAndCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.info = core.VideoInfo(
            width=1920,
            height=1080,
            fps=25.0,
            duration=60.0,
            video_codec="h264",
            audio_codecs=("aac",),
            pix_fmt="yuv420p",
            container="mov,mp4",
            hdr=False,
            hdr_reasons=(),
        )

    def test_srt_filter_style_and_pad_order(self) -> None:
        style = core.SubtitleStyle(font_name="Microsoft YaHei", font_size=22, margin_v=30)
        vf = core.build_subtitle_filter("sub.srt", ".srt", style)
        self.assertIn("subtitles=sub.srt", vf)
        self.assertIn("force_style=", vf)
        self.assertIn("FontName=Microsoft YaHei", vf)
        self.assertIn("FontSize=22", vf)
        self.assertIn("MarginV=30", vf)
        self.assertIn("PrimaryColour=&H00FFFFFF", vf)
        self.assertLess(vf.index("subtitles="), vf.index("pad="))
        self.assertIn("pad=ceil(iw/2)*2:ceil(ih/2)*2", vf)
        self.assertNotIn("scale", vf)
        self.assertNotRegex(vf, r"[A-Za-z]:[\\/]")
        self.assertNotIn("\\", vf)

    def test_filter_arguments_stay_ascii(self) -> None:
        style = core.SubtitleStyle(font_name="微软雅黑")
        vf = core.build_subtitle_filter("sub.srt", ".srt", style)
        self.assertTrue(vf.isascii(), vf)
        self.assertIn("FontName=Microsoft YaHei", vf)

    def test_ass_filter_keeps_original_style(self) -> None:
        vf = core.build_subtitle_filter("sub.ass", ".ass", core.SubtitleStyle())
        self.assertIn("subtitles=sub.ass", vf)
        self.assertNotIn("force_style", vf)
        self.assertIn("pad=", vf)

    def test_mp4_command(self) -> None:
        vf = core.build_subtitle_filter("sub.srt", ".srt", core.SubtitleStyle())
        cmd = core.build_ffmpeg_command(
            "ffmpeg.exe", "C:/in/movie.mp4", "C:/out/movie_硬字幕.mp4", vf, self.info, 18
        )
        self.assertEqual(cmd[:3], ["ffmpeg.exe", "-hide_banner", "-nostdin"])
        self.assertIn("libx264", cmd)
        self.assertIn("medium", cmd)
        self.assertIn("18", cmd)
        self.assertIn("-c:a", cmd)
        self.assertIn("copy", cmd)
        self.assertIn("-movflags", cmd)
        self.assertIn("+faststart", cmd)
        self.assertIn("-fps_mode", cmd)
        self.assertIn("passthrough", cmd)
        self.assertNotIn("-vsync", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("0:a?", cmd)
        self.assertNotIn("scale", " ".join(cmd).casefold())
        self.assertEqual(cmd[-1], str(Path("C:/out/movie_硬字幕.mp4").resolve()))

    def test_mkv_does_not_get_faststart(self) -> None:
        vf = core.build_subtitle_filter("sub.ass", ".ass", core.SubtitleStyle())
        cmd = core.build_ffmpeg_command(
            "ffmpeg.exe", "C:/in/movie.mkv", "C:/out/movie_硬字幕.mkv", vf, self.info, 20
        )
        self.assertNotIn("-movflags", cmd)

    def test_crf_out_of_range_rejected(self) -> None:
        vf = core.build_subtitle_filter("sub.srt", ".srt", core.SubtitleStyle())
        with self.assertRaises(core.BurnError):
            core.build_ffmpeg_command("ffmpeg.exe", "a.mp4", "b.mp4", vf, self.info, 52)


class OutputNamingTests(unittest.TestCase):
    def test_default_name(self) -> None:
        path = core.default_output_path("C:/videos/movie.mp4", "D:/out")
        self.assertEqual(path.name, "movie_硬字幕.mp4")

    def test_numbering_on_decline(self) -> None:
        with workspace_tmp() as temp:
            folder = Path(temp)
            video = folder / "movie.mp4"
            video.write_bytes(b"x")
            (folder / "movie_硬字幕.mp4").write_bytes(b"old")
            result = core.choose_output_path(video, folder, overwrite=False)
            self.assertEqual(result.name, "movie_硬字幕_1.mp4")
            result.write_bytes(b"new")
            result2 = core.choose_output_path(video, folder, overwrite=False)
            self.assertEqual(result2.name, "movie_硬字幕_2.mp4")

    def test_accept_overwrite(self) -> None:
        with workspace_tmp() as temp:
            folder = Path(temp)
            video = folder / "movie.mp4"
            video.write_bytes(b"x")
            target = folder / "movie_硬字幕.mp4"
            target.write_bytes(b"old")
            result = core.choose_output_path(video, folder, overwrite=True)
            self.assertEqual(result, target)

    def test_never_input(self) -> None:
        with workspace_tmp() as temp:
            folder = Path(temp)
            video = folder / "x.mp4"
            video.write_bytes(b"x")
            result = core.choose_output_path(video, folder, overwrite=True)
            self.assertFalse(core.same_path(result, video))


class CleanupTests(unittest.TestCase):
    def test_cleanup_only_job_directories(self) -> None:
        with workspace_tmp() as temp:
            work = Path(temp) / "work"
            work.mkdir()
            job = work / "job_20260101_000000_abcdef"
            job.mkdir()
            (job / "sub.srt").write_text("x", encoding="utf-8")
            keep = work / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            other = work / "job_not_a_dir.txt"
            other.write_text("x", encoding="utf-8")

            failures = core.cleanup_stale_jobs(work)
            self.assertEqual(failures, [])
            self.assertFalse(job.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(other.exists())


class ErrorClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.info = core.VideoInfo(
            width=1920,
            height=1080,
            fps=25.0,
            duration=60.0,
            video_codec="h264",
            audio_codecs=("pcm_s16le",),
            pix_fmt="yuv420p",
            container="mov,mp4",
            hdr=False,
            hdr_reasons=(),
        )

    def test_audio_container_error_is_explicit(self) -> None:
        raw = (
            "[mp4 @ 000001] Could not find tag for codec pcm_s16le in stream #1, "
            "codec not currently supported in container\n"
            "Could not write header for output file #0"
        )
        message = core._ffmpeg_error_message(raw, self.info)
        self.assertIn("当前容器不支持该音频编码，无法无损复制音频", message)
        self.assertNotIn("转码", message)

    def test_generic_error_preserves_raw(self) -> None:
        message = core._ffmpeg_error_message("Unknown error occurred", self.info)
        self.assertIn("视频编码失败", message)


def _ffmpeg_test_support() -> tuple[bool, str]:
    binary = ROOT / "ffmpeg.exe"
    if not binary.is_file() or binary.stat().st_size <= 1_000_000:
        return False, "项目根目录没有可用的 ffmpeg.exe"
    try:
        help_text = subprocess.run(
            [str(binary), "-hide_banner", "-h", "full"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        ).stdout
    except OSError as exc:
        return False, f"无法运行 ffmpeg.exe：{exc}"
    if "fps_mode" not in help_text:
        return False, "本地 ffmpeg 过旧（需要 FFmpeg 5.1+ 的 -fps_mode）"
    return True, ""


class JobLifecycleIntegrationTest(unittest.TestCase):
    """使用项目根目录的测试字体/ffmpeg 环境时（可选）进行真实编码验证。"""

    @classmethod
    def setUpClass(cls) -> None:
        supported, reason = _ffmpeg_test_support()
        if not supported:
            raise unittest.SkipTest(reason)

    def test_end_to_end_burn_and_cleanup(self) -> None:
        with workspace_tmp() as temp:
            folder = Path(temp)
            video = folder / "样例 视频.mp4"
            subtitle = folder / "样例 视频.srt"
            output_dir = folder / "输出 目录"
            output_dir.mkdir()
            gen = [
                str(ROOT / "ffmpeg.exe"),
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", str(video),
            ]
            subprocess.run(gen, check=True, capture_output=True)
            subtitle.write_text(
                "1\n00:00:00,200 --> 00:00:01,800\n你好，烧录测试。\n",
                encoding="utf-8",
            )
            info = core.VideoInfo(320, 240, 25.0, 2.0, "h264", ("aac",), "yuv420p", "mp4", False, ())
            job = core.BurnJob(
                ffmpeg=ROOT / "ffmpeg.exe",
                ffprobe=ROOT / "ffprobe.exe",
                video_path=video,
                subtitle_path=subtitle,
                output_path=output_dir / "样例 视频_硬字幕.mp4",
                base_dir=folder,
                video_info=info,
                cancel_event=__import__("threading").Event(),
            )
            result = core.run_burn_job(job)
            self.assertTrue(result.output_path.is_file())
            self.assertGreater(result.output_path.stat().st_size, 1000)
            self.assertFalse((folder / "work").exists() and any((folder / "work").glob("job_*")))
            leftovers = list(output_dir.glob(".*part*"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
