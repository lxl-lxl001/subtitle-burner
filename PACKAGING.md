# 字幕烧录工具：方法 A（GitHub 网页上传）打包教程

> 本教程对应你选择的**方法 A：全程使用 GitHub 网页上传，不安装 Git、不安装 Python、不安装 PyInstaller、不安装 FFmpeg**。
>
> 最后你会下载到一个绿色便携文件夹，里面有：
>
> ```text
> 字幕烧录工具\
> ├─ 字幕烧录工具.exe   ← 双击这个
> ├─ ffmpeg.exe         ← 由 workflow 自动下载并放进文件夹
> ├─ ffprobe.exe
> └─ Python / Tk 运行库……
> ```
>
> 把**整个文件夹**拷到任意 Win10/Win11 64 位电脑，双击 exe 即可使用。

---

## 0. 方法 A 的总流程

```text
下载源码 zip → 解压
      ↓
GitHub 新建空仓库
      ↓
网页上传解压后的全部文件
      ↓
检查 app.spec 和 .github/workflows/build-windows.yml 在仓库根目录
      ↓
提交后自动触发 GitHub Actions
      ↓
云端自动下载 static FFmpeg/FFprobe → 自动打包
      ↓
在 Actions 页面下载 artifact zip
      ↓
解压得到“字幕烧录工具”文件夹
      ↓
拷贝整个文件夹到目标电脑，双击 exe
```

你在网页上传时**不需要上传 ffmpeg.exe 和 ffprobe.exe**。workflow 会自动下载 static 版本并把它们放进最终文件夹。

---

## 1. 下载并解压源码

1. 在对话中下载：

   [字幕烧录工具_源码.zip](字幕烧录工具_源码.zip)

2. 在 Windows 上右键 zip → `全部解压缩`。
3. 打开解压后的文件夹，应该能看到：

   ```text
   字幕烧录工具_源码\
   ├─ .github\
   │  └─ workflows\
   │     └─ build-windows.yml
   ├─ tests\
   │  └─ test_core.py
   ├─ app.py
   ├─ core.py
   ├─ gui.py
   ├─ app.spec
   ├─ requirements.txt
   ├─ README.md
   ├─ PACKAGING.md
   └─ .gitignore
   ```

4. Windows 资源管理器可能默认隐藏点开头的文件/文件夹，先打开：

   `查看` → `显示` → 勾选 `隐藏的项目`

   确保能看到 `.github`。

> 注意：不要停在 zip 内部直接拖拽上传。一定要先解压，上传的是解压后的真实文件。

---

## 2. 新建 GitHub 空仓库

1. 登录 <https://github.com/>。
2. 右上角 `+` → `New repository`。
3. `Repository name` 填一个英文名，例如：

   ```text
   subtitle-burner
   ```

4. 选择 `Public` 或 `Private` 都可以。
5. **不要**勾选 `Add a README file`、`.gitignore`、`license`。
6. 点 `Create repository`。

创建完成后，页面上会显示一个空仓库和上传入口。

---

## 3. 用网页上传全部项目文件

### 3.1 进入上传页面

在空仓库页面点击：

```text
Add file → Upload files
```

### 3.2 上传哪些文件

打开第 1 步解压出来的文件夹，按 `Ctrl+A` 全选以下内容：

```text
.github
tests
.gitignore
app.py
core.py
gui.py
app.spec
requirements.txt
README.md
PACKAGING.md
```

然后把它们**一起拖进浏览器的上传区域**。

> 不要上传 `字幕烧录工具_源码.zip` 这个压缩包本身。
> GitHub 不会自动解压它，只会把它当普通附件，Actions 找不到 app.py 和 app.spec。

### 3.3 检查上传列表

上传区域应该能看到类似：

```text
.github/workflows/build-windows.yml
tests/test_core.py
.gitignore
app.py
core.py
gui.py
app.spec
requirements.txt
README.md
PACKAGING.md
```

如果浏览器没有识别出 `.github` 文件夹，不要慌，按第 5 步手动创建 workflow 即可。

### 3.4 提交

页面底部：

1. `Commit changes` 区域可以保持默认。
2. 点绿色按钮 `Commit changes`。
3. 确认提交到 `main` 分支（新建仓库的默认分支）。

提交后，如果 `.github/workflows/build-windows.yml` 已经上传成功，Actions 会自动开始构建。

---

## 4. 检查仓库根目录是否正确

回到仓库首页，确认看到：

```text
.github/
tests/
app.py
core.py
gui.py
app.spec
requirements.txt
README.md
PACKAGING.md
```

**关键：`app.py`、`app.spec`、`requirements.txt` 必须在仓库最外层，不能多套一层文件夹。**

错误示例：

```text
subtitle-burner/
└─ 字幕烧录工具_源码/
   ├─ app.py
   └─ app.spec
```

正确示例：

```text
subtitle-burner/
├─ app.py
├─ app.spec
├─ requirements.txt
└─ .github/
```

如果多套了一层文件夹，请删掉重新按第 3 步上传，或把内层文件移动到根目录。

---

## 5. 如果 `.github` 没上传成功：手动创建 workflow

这是网页上传最常见的坑：点开头的隐藏文件夹偶尔会被浏览器忽略。
检查仓库顶部是否有 `Actions` 标签、且文件树里是否有：

```text
.github/workflows/build-windows.yml
```

如果没有，就手动创建：

1. 仓库首页点 `Add file` → `Create new file`。
2. 文件名输入：

   ```text
   .github/workflows/build-windows.yml
   ```

   直接输入斜杠，GitHub 会自动建目录。
3. 把下面的完整内容粘贴进去：

```yaml
name: Build Windows Portable App

on:
  workflow_dispatch:
  push:
    branches:
      - main
      - master

permissions:
  contents: read

jobs:
  build:
    name: Build (Windows x64)
    runs-on: windows-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.12 (x64)
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          architecture: "x64"
          cache: "pip"

      - name: Install build dependencies
        shell: pwsh
        run: |
          $ErrorActionPreference = "Stop"
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt

      # 仓库里已经放了 ffmpeg.exe/ffprobe.exe 时直接使用；
      # 没放时自动下载 BtbN 的 static GPL 构建，保证网页上传流程开箱即可构建。
      - name: Prepare static ffmpeg and ffprobe
        shell: pwsh
        run: |
          $ErrorActionPreference = "Stop"

          if ((Test-Path ".\ffmpeg.exe") -and (Test-Path ".\ffprobe.exe")) {
            Write-Host "Using ffmpeg.exe / ffprobe.exe from repository."
          } else {
            $archive = Join-Path $env:RUNNER_TEMP "ffmpeg-win64-gpl.zip"
            $extract = Join-Path $env:RUNNER_TEMP "ffmpeg-win64-gpl"
            $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

            Write-Host "Downloading static FFmpeg from $url"
            Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
            Expand-Archive -Path $archive -DestinationPath $extract -Force

            $ffmpegExe = Get-ChildItem -Path $extract -Recurse -Filter "ffmpeg.exe" |
              Select-Object -First 1
            if ($null -eq $ffmpegExe) {
              throw "Downloaded archive does not contain ffmpeg.exe"
            }
            $binDir = $ffmpegExe.DirectoryName
            Copy-Item (Join-Path $binDir "ffmpeg.exe") ".\ffmpeg.exe" -Force
            Copy-Item (Join-Path $binDir "ffprobe.exe") ".\ffprobe.exe" -Force
          }

          & ".\ffmpeg.exe" -hide_banner -version
          & ".\ffprobe.exe" -hide_banner -version

          # 尽早失败，避免构建出无法烧录字幕的包。
          $filters = (& ".\ffmpeg.exe" -hide_banner -filters 2>&1) -join "`n"
          if ($filters -notmatch "\ssubtitles\s") {
            throw "ffmpeg.exe 缺少 libass 的 subtitles 滤镜，请换 static 完整构建。"
          }
          $encoders = (& ".\ffmpeg.exe" -hide_banner -encoders 2>&1) -join "`n"
          if ($encoders -notmatch "libx264") {
            throw "ffmpeg.exe 缺少 libx264 编码器，请换带 libx264 的 static 构建。"
          }
          $ffmpegHelp = (& ".\ffmpeg.exe" -hide_banner -h full 2>&1) -join "`n"
          if ($ffmpegHelp -notmatch "fps_mode") {
            throw "ffmpeg 版本过旧：需要 FFmpeg 5.1+（使用 -fps_mode passthrough）。"
          }

      # tests/ 作为可选文件：网页上传时若漏掉 tests 文件夹，也不影响打包。
      - name: Run tests
        if: ${{ hashFiles('tests/test_core.py') != '' }}
        shell: pwsh
        run: |
          $ErrorActionPreference = "Stop"
          python -m unittest discover -s tests -v

      - name: Build with PyInstaller (onedir)
        shell: pwsh
        run: |
          $ErrorActionPreference = "Stop"
          python -m PyInstaller --noconfirm --clean app.spec

      - name: Verify portable folder layout
        shell: pwsh
        run: |
          $ErrorActionPreference = "Stop"
          $appDir = "dist\字幕烧录工具"
          $exe = Join-Path $appDir "字幕烧录工具.exe"
          if (-not (Test-Path $exe)) { throw "Missing: $exe" }
          if (-not (Test-Path (Join-Path $appDir "ffmpeg.exe"))) {
            throw "ffmpeg.exe 必须与 exe 位于同一目录（检查 spec 的 contents_directory='.'）"
          }
          if (-not (Test-Path (Join-Path $appDir "ffprobe.exe"))) {
            throw "ffprobe.exe 必须与 exe 位于同一目录（检查 spec 的 contents_directory='.'）"
          }
          Get-ChildItem $appDir | Select-Object Name, Length
          Write-Host "Portable folder verified: $appDir"

      - name: Upload portable folder as artifact
        uses: actions/upload-artifact@v4
        with:
          name: SubtitleBurner-Windows-x64
          # 上传 dist/ 而不是 dist/字幕烧录工具/，这样解压 artifact 后
          # 会得到完整的外层文件夹“字幕烧录工具”。
          path: dist/
          if-no-files-found: error
          retention-days: 30
```

4. 页面底部点 `Commit changes`。
5. 这一次提交也会触发构建。

---

## 6. 如果 app.spec 没上传：手动补上

正常情况下，`app.spec` 已经在源码 zip 里，上传后应位于仓库根目录。

若仓库文件树里没有 `app.spec`：

1. `Add file` → `Create new file`。
2. 文件名填：

   ```text
   app.spec
   ```

3. 粘贴完整内容：

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 打包配置（英文文件名，避免 workflow 引号问题）。

重要：
1. 使用 --onedir，不使用 --onefile。
2. ffmpeg.exe 与 ffprobe.exe 必须放在本文件所在目录（仓库根目录）。
3. EXE 的 contents_directory='.' 让 onedir 采用平铺布局，保证 ffmpeg.exe /
   ffprobe.exe 与“字幕烧录工具.exe”位于同一目录，符合程序运行期定位规则。
4. 最终可执行文件名为“字幕烧录工具.exe”，但 spec 文件名保持 app.spec。
"""

from pathlib import Path


for _name in ("ffmpeg.exe", "ffprobe.exe"):
    if not Path(_name).is_file():
        raise SystemExit(
            f"[app.spec] 缺少 {_name}。请先把 static 版本的 ffmpeg.exe 和 "
            "ffprobe.exe 放到仓库根目录（与 app.spec 同一级）。"
        )

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[
        ("ffmpeg.exe", "."),
        ("ffprobe.exe", "."),
    ],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="字幕烧录工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="字幕烧录工具",
)
```

4. `Commit changes`。

同样检查 `requirements.txt` 是否存在，内容应为：

```text
# 运行期仅依赖 Python 标准库（tkinter）。
# 以下为构建期依赖，仅在 PyInstaller 打包环境/云端 workflow 中安装。

pyinstaller>=6.10,<7
```

缺了就同样用 `Add file` → `Create new file`，文件名 `requirements.txt`，粘贴后提交。

---

## 7. 观察 Actions 构建

1. 点仓库顶部的 `Actions`。
2. 左侧选择：

   ```text
   Build Windows Portable App
   ```

3. 点最新一次运行，再点 `Build (Windows x64)`。
4. 正常情况下依次变绿：
   - `Checkout repository`
   - `Set up Python 3.12 (x64)`
   - `Install build dependencies`
   - `Prepare static ffmpeg and ffprobe` ← 自动下载 FFmpeg
   - `Run tests`（若 tests 上传了）
   - `Build with PyInstaller (onedir)`
   - `Verify portable folder layout`
   - `Upload portable folder as artifact`

5. 第一次通常需要 5–15 分钟，主要时间花在下载 FFmpeg 和 PyInstaller 打包。

### 如果 Actions 没自动开始

1. `Actions` → `Build Windows Portable App`。
2. 右侧点 `Run workflow`。
3. 选择 `main` 分支，再点绿色 `Run workflow`。

### 如果构建失败

点开红色步骤，重点看：

- `Prepare static ffmpeg and ffprobe`：下载 FFmpeg 失败，或版本不满足。
- `Build with PyInstaller`：app.spec 缺文件、文件名不对。
- `Verify portable folder layout`：ffmpeg/ffprobe 没跟 exe 放在一起。

把红色报错原文保留，对照第 11 节。

---

## 8. 下载 artifact

1. 打开这次成功的运行页面。
2. 页面底部找到 `Artifacts`。
3. 点击：

   ```text
   SubtitleBurner-Windows-x64
   ```

4. 下载得到 `SubtitleBurner-Windows-x64.zip`。
5. 全部解压。

解压后应得到：

```text
字幕烧录工具\
├─ 字幕烧录工具.exe
├─ ffmpeg.exe
├─ ffprobe.exe
├─ python312.dll
└─ 其他运行库……
```

**三个关键文件必须同层。**

> artifact 保留时间：本 workflow 显式设置了 `retention-days: 30`，即 **30 天后下载入口会过期**。
> 想长期保存，见第 10 节发布 GitHub Release。

---

## 9. 在目标电脑上运行

1. 把整个 `字幕烧录工具` 文件夹拷到 Win10/Win11 64 位电脑。
2. 双击 `字幕烧录工具.exe`。
3. 若 Windows SmartScreen 拦截：
   - 点 `更多信息` → `仍要运行`。
   - 这是未签名免费工具的正常提示；程序不联网上传视频。
4. 按界面操作：
   - 选择视频；
   - 确认自动匹配的 `.srt` / `.ass`；
   - 选择输出目录；
   - 点开始；
   - 完成后到输出目录双击新视频。

> 只拷 exe 是不行的。运行库里包含 Python、Tk，以及 ffmpeg/ffprobe，必须整个文件夹一起拷。

---

## 10. 长期保存：发布 Release（可选）

artifact 30 天后会过期。要长期分发：

1. 下载并解压 artifact zip。
2. 把解压出的 `字幕烧录工具` 文件夹**重新压缩成 zip**。
3. 仓库首页右侧点 `Releases` → `Draft a new release`。
4. 填 tag，例如 `v1.0.0`。
5. 标题填 `字幕烧录工具 v1.0.0`。
6. 把刚压缩的 zip 拖进附件区域。
7. 点 `Publish release`。

Release 附件不会像 Actions artifact 那样 30 天过期。

---

## 11. 方法 A 常见翻车点

1. **把源码 zip 直接上传，没有解压**
   - GitHub 不会解压它。仓库里必须有 `app.py`、`app.spec`、`.github/workflows/build-windows.yml`，不能只有一个 zip。

2. **文件被套在子文件夹里**
   - 仓库根目录必须直接是 `app.py`、`app.spec`、`requirements.txt`。
   - 如果变成 `repo/字幕烧录工具_源码/app.py`，Actions 会因为找不到 `app.spec` 或入口脚本失败。

3. **`.github` 文件夹没上传**
   - 表现：`Actions` 里没有 `Build Windows Portable App`。
   - 解决：按第 5 步手动 `Create new file` 创建 `.github/workflows/build-windows.yml`。

4. **没上传 ffmpeg.exe / ffprobe.exe**
   - 方法 A 不需要你上传，workflow 会自动下载。
   - 不要因此手动塞入大型 exe，容易触发 GitHub 100 MB 单文件限制。

5. **上传了 shared 版 FFmpeg 或文件缺 libass/libx264**
   - 方法 A 默认下载 BtbN 的 static GPL 构建，通常没问题。
   - 如果你改用自己上传的 FFmpeg，必须保留 `ffmpeg.exe`、`ffprobe.exe` 两个文件，且支持 `subtitles`、`libx264`、`fps_mode`。

6. **spec 文件名改成中文**
   - 必须是 `app.spec`。最终 exe 的中文名由 spec 的 `name` 控制，不用改 spec 文件名。

7. **artifact 过期**
   - 本 workflow 设置 30 天保留。过期后重新运行 workflow，或使用 Release。

8. **只下载/只拷贝了 exe**
   - 必须拷贝整个文件夹，里面有运行库和 ffmpeg/ffprobe。

9. **分支不是 main/master**
   - workflow 只在 `main`、`master` 的 push 时自动触发，其他分支需要手动到 Actions 点 `Run workflow`。

10. **Private 仓库 Actions 分钟数用完**
    - 免费账户有额度限制。构建卡住/被跳过时检查账号的 Actions 用量。

11. **杀毒软件拦截**
    - 未签名 exe 可能被 Defender 扫描。确认整个文件夹完整，必要时加入信任。

12. **网页上传时漏了 `requirements.txt`**
    - 会卡在 `pip install -r requirements.txt`。
    - 用 `Add file` → `Create new file` 补上，内容见第 6 节。

---

## 12. 一页版最短流程

1. 下载并解压 [字幕烧录工具_源码.zip](字幕烧录工具_源码.zip)。
2. GitHub 建空仓库。
3. `Add file` → `Upload files`，把解压后的全部文件/文件夹拖进去。
4. `Commit changes`。
5. 检查仓库根目录有 `app.py`、`app.spec`、`requirements.txt`、`.github/workflows/build-windows.yml`。
6. 没有 `.github` 就按第 5 步手动创建 workflow。
7. 等 Actions 变绿（自动下载 FFmpeg，自动打包）。
8. Actions → 最新运行 → Artifacts → 下载 `SubtitleBurner-Windows-x64`。
9. 解压，得到 `字幕烧录工具` 文件夹。
10. 整个文件夹拷到 Win10/11 电脑，双击 `字幕烧录工具.exe`。

完成。
