# 字幕烧录工具（Windows）

把外挂字幕 **永久烧进视频画面**，输出一个无需字幕文件、双击即可观看的视频。

- 支持 `.srt` / `.ass`；
- 自动匹配视频同目录下同主文件名的字幕，SRT 优先，双字幕可切换；
- 视频用 `libx264` 重新编码，音频 **原样复制**；
- 分辨率、帧率、SAR/DAR 不变，不出现拉伸/变形；
- 中文、空格路径安全处理；
- 本地运行，不上传任何文件。

> 云端一键打包（不需要本地 Python / PyInstaller / FFmpeg）请看 **[PACKAGING.md](PACKAGING.md)**。

---

## 1. 用户怎么用

1. 双击 `字幕烧录工具.exe`。
2. 点“选择视频…”，选中 mp4 / mkv / avi 等文件。
3. 程序会自动找同目录、同主文件名的 `.srt` / `.ass`：
   - 两个都有时默认选 SRT，可在下拉框切换；
   - 找不到时点“手动选择…”。
4. 选择输出目录。
5. 点“开始烧录”，等待进度完成。可随时“取消”。
6. 到输出目录双击新视频即可。字幕已经在画面里了，不再需要字幕文件。

默认输出名：

```text
movie.mp4  →  movie_硬字幕.mp4
```

输出目录已有同名文件时会询问是否覆盖；拒绝覆盖会自动改用 `_1`、`_2` 等序号。**绝不会覆盖输入原视频。**

---

## 2. 项目结构

```text
字幕烧录工具/
├─ app.py                         # 入口（DPI、日志、启动清理）
├─ core.py                        # 字幕/探测/FFmpeg/临时目录/输出规则
├─ gui.py                         # tkinter 界面
├─ app.spec                       # PyInstaller onedir 配置（英文文件名）
├─ requirements.txt               # 构建期依赖（PyInstaller）
├─ tests/
│  └─ test_core.py                # 单元测试 + 可选真实编码测试
├─ .github/
│  └─ workflows/
│     └─ build-windows.yml        # GitHub Actions 云端构建
├─ README.md
└─ PACKAGING.md                   # 保姆式云端打包教程
```

运行/构建时还会自动生成：

```text
logs/app.log                      # 日志，轮转保存
config/settings.json              # 上次输出目录、CRF、字幕样式
work/job_<时间戳>_<随机串>/        # 仅放临时的 sub.srt / sub.ass，任务结束即删除
```

这些目录都在程序（或脚本）目录下，**不写注册表、不写 AppData、不使用系统 %TEMP%**。

---

## 3. 开发态运行

要求：

- Windows 10 / 11 64 位；
- Python 3.10+ 64 位（推荐 3.12），安装时勾选 Tcl/Tk；
- 完整 static 版 `ffmpeg.exe` 和 `ffprobe.exe`（同一构建，必须带 libx264、libass，且为 FFmpeg 5.1+）。

把两个 exe 放到项目根目录（与 `app.py` 同级），然后：

```powershell
python app.py
```

开发态二进制目录 = 脚本所在目录。
打包态二进制目录 = `sys.executable`（exe）所在目录。

`ffprobe.exe` 与 `ffmpeg.exe` 必须在同一目录。

### FFmpeg 从哪里获取

推荐使用下面任一 static 构建（解压后取 `bin\ffmpeg.exe`、`bin\ffprobe.exe`）：

- BtbN：<https://github.com/BtbN/FFmpeg-Builds/releases>（选 `win64-gpl` 的 **非 shared** zip）
- gyan.dev：<https://www.gyan.dev/ffmpeg/builds/>（选 *release essentials* 或 *full*）

只需要两个 exe，不需要安装，也不要只复制 DLL 版或缺少 libass 的“精简版”。

---

## 4. 测试

```powershell
python -m unittest discover -s tests -v
```

- 无 `ffmpeg.exe` 时，真实编码测试会自动跳过；
- 根目录有可用的 `ffmpeg.exe` 时，会实际生成视频、烧录、检查输出并清理临时文件。

---

## 5. 本地打包（可选）

```powershell
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean app.spec
```

产物：

```text
dist\字幕烧录工具\
├─ 字幕烧录工具.exe
├─ ffmpeg.exe
├─ ffprobe.exe
└─ ...（Python/Tk 运行库等，很多文件是正常的）
```

分发给别人时**必须拷走整个文件夹**，不能只拷 exe。

更推荐用 [PACKAGING.md](PACKAGING.md) 的 GitHub Actions 云端打包，不需要在本地安装任何 Python 工具。

---

## 6. 画质与兼容性保证

| 项目 | 处理方式 |
|---|---|
| 画面尺寸 | 不缩放。奇数宽高最后用 `pad` 补 1 像素到偶数，满足 H.264 |
| 画面比例 | 不修改 SAR/DAR，不使用 `scale` |
| 视频编码 | `-c:v libx264 -preset medium -crf 18`（CRF 可调） |
| 音频 | `-c:a copy`，绝不静默转码 |
| 帧率 | 不设置目标帧率，`-fps_mode passthrough` 保留原始时间戳 |
| 容器 | 跟随输入扩展名；mp4 附加 `-movflags +faststart` |
| 字幕滤镜 | `subtitles=...` 在前，`pad=ceil(iw/2)*2:ceil(ih/2)*2` 在后 |
| SRT 样式 | `force_style`：Microsoft YaHei / 20 / 白色 / 底部边距 28（界面可调，字体请填英文注册名） |
| ASS 样式 | 完整保留原样式，不强制覆盖；缺字体时由 libass 系统字体回退 |

路径安全设计：

- 字幕先检测编码并转成 UTF-8；
- 保留扩展名复制到 `work/job_.../sub.srt` 或 `sub.ass`；
- ffmpeg 的 `cwd` 设为该 job 目录；
- `subtitles=sub.srt` 只写相对 ASCII 文件名；
- 输入视频、输出视频传绝对路径。

所以程序目录、视频名、字幕名含中文或空格都不会触发 Windows `subtitles` 滤镜的路径转义坑。

### HDR

检测到 `bt2020`、`smpte2084`、`arib-std-b67`、Dolby Vision 等标记时会提示：重新编码为 H.264 后可能有色彩偏差。普通 SDR 视频正常处理，不弹此警告。

### 音频无法复制时

若输出容器不支持原音频编码（例如某些容器里的 PCM / TrueHD），程序会明确提示：

> 当前容器不支持该音频编码，无法无损复制音频。

此时**不会自动转码**。请换用支持该音频的容器（MKV 最稳），而不是牺牲音质。

---

## 7. 日志与排错

- 日志：`logs/app.log`（2 MB 轮转，最多 3 份备份）
- 错误对话框可复制完整信息，也可直接打开日志目录。
- 启动时会清理 `work/job_*` 残留；清理失败只提示路径，不阻塞启动。
- 临时目录清理失败只影响一个很小的字幕临时目录，不会残留视频成品。

程序所在目录不可写时（例如 `C:\Program Files`）：

1. 临时字幕自动降级到 **输出目录\.work\**；
2. 日志/配置可能无法写入程序目录，界面会提示；
3. 仍不可写时会提示把整个程序移到可写目录。

---

## 8. 已知限制

- 只烧录画面字幕，不保留原视频里可能存在的多条内封字幕轨；这是“硬字幕”输出，不需要字幕轨。
- 对含旋转元数据的手机视频会提示方向问题；输出方向以播放器自动旋转为准。
- 不提供 HDR→SDR 的 tone mapping，HDR 视频可能有偏色。
- 使用 libx264 必然重新编码视频；这是烧字幕的必要代价。音频始终不重编码。

---

## 9. 许可与第三方组件

- 本项目源代码使用 Python 标准库（tkinter），无运行期第三方 Python 依赖。
- 打包时内置的 FFmpeg/FFprobe 属于第三方软件，请遵守你所用 FFmpeg 构建的许可证（通常为 GPL）。static 构建来自 BtbN / gyan.dev，分发时建议一并提供 FFmpeg 的来源与许可证信息。
