---
name: lumenx-build
description: LumenX Studio desktop build workflow for macOS DMG and Windows EXE packaging.
---

# LumenX Studio 桌面构建

正式桌面路径为 main.py / PyInstaller。构建成功、DMG 生成与实际安装/冷启动验收是不同证据；不要将 Tauri 或普通浏览器结果替代正式包验收。

## macOS：先准备，再使用隔离入口

在独占的固定提交源码副本上工作。预先准备 Python 3.11+、项目依赖、同一解释器内的 PyInstaller、Xcode Command Line Tools，以及同版 FFmpeg/FFprobe。构建工具可放在临时 venv，并只读引用已验证的项目依赖；不要升级共享 venv 或复用 PATH 中未知 Python 的 PyInstaller。

先对选定源码完成测试、类型检查与默认生产前端构建，确认静态目录使用 /static/ 路径。不要使用 TAURI_BUILD 或 DOCKER_BUILD 的另一种静态产物。按模型目录工作流验证 YAML 与生成文件一致；实际生成模型、PDF 等依赖缺口须单列，不通过打包成功隐藏。

正式图标使用既有品牌的 `src-tauri/icons/icon.icns`，缺失或符号链接会明确失败，不回退根旧图标。需要再生成时，`create_icon.sh` 从自身目录定位同目录 `icon.png`，使用独占临时目录完成十种尺寸转换后原子替换 icns；失败保留原目标。打包不会隐式再生图标，也不修改旧包。

图标脚本的 4 项回归独立运行，只用标准库、真实 shell 和假转换工具：

```bash
python3 -I -B -S scripts/check_macos_icon_recipe.py \
  --tmp-root /absolute/new-owned-icon-check
```

临时根必须尚不存在，并位于源码和 Python 环境之外；检查器保留结果与失败日志。不能从后端隔离测试进程调用，`LUMENX_TEST_ISOLATED=1` 会明确拒绝；打包计划测试仍走现有后端隔离入口。CI 单独执行该检查，4 项不计入 pytest 数量。Ubuntu 替身检查不等于 macOS 真实转换、正式出包或 Dock 视觉验收。

构建入口不会安装依赖、运行前端构建、读取 .env/个人 output/config.json，或清理共享目录。它只接受明确的已准备输入：

```bash
./build_mac.sh --python /absolute/build-venv/bin/python \
  --source /absolute/fixed-source \
  --static /absolute/verified-static \
  --ffmpeg /absolute/tools/ffmpeg \
  --ffprobe /absolute/tools/ffprobe \
  --output /absolute/new-owned-build \
  --plan --dmg
```

此命令只校验并输出 JSON，不创建输出或执行构建工具。JSON 的 dependencies 会列出当前解释器可见的声明依赖版本；包括现有默认 LLM 路径实际需要的 openai。缺项会阻止真实构建，不会触发安装；版本存在也不等于完整运行依赖闭包。检查清单中的来源、SHA、资源和输出位置；构建目录必须尚不存在，且位于源码、静态资源和 Python 环境之外。

确认同一组输入后执行：

```bash
./build_mac.sh --python /absolute/build-venv/bin/python \
  --source /absolute/fixed-source \
  --static /absolute/verified-static \
  --ffmpeg /absolute/tools/ffmpeg \
  --ffprobe /absolute/tools/ffprobe \
  --output /absolute/new-owned-build \
  --dmg
```

资源包括静态前端、模型目录元信息/families/schema/generated、生成目录精确引用的本地 API 快照，以及 style_presets.json；不将整个 src/docs/output 当作数据目录打包。Python 模块由 PyInstaller 分析。两个媒体工具必须同版且都可执行；版本日志、输入清单和构建日志保留在独占输出中。既有 build.spec.template 已退役，不能用它打包环境文件。

预期输出：

```text
/absolute/new-owned-build/plan.json
/absolute/new-owned-build/pyinstaller.log
/absolute/new-owned-build/package/LumenX Studio.app
/absolute/new-owned-build/LumenX Studio.dmg
```

省略 --dmg 仅生成 app。入口移动 app 到专用 package 目录后生成 DMG，不额外复制多个 app，也不会卸载任何已有卷。PyInstaller 自身的分析/收集目录仍占空间；4 GiB 空闲只是启动下限，不是空间充足保证，DMG 前还按实际 app 大小重新检查。失败保留证据，重试换一个新的独占输出根；清理仅限已确认属于本次构建的目录，不能运行项目根下通配符清理。

构建使用固定的 com.lumenx.studio 作为 Bundle ID，展示名称仍为 LumenX Studio；不能让含空格的展示名称成为签名标识。PyInstaller 的 bundle 签名失败必须中断构建，不能以 warning 继续生成可交付 App/DMG。严格签名开关不等于 Developer ID 签名、公证或冷启动验收。

下一步必须检查包内资源、FFmpeg/FFprobe 及所有 Mach-O 依赖的重定位/签名，确认不依赖宿主 Homebrew/venv，再在临时用户数据目录实际启动、退出、重开并验证本地媒体。远端入口模式另验；不要直接使用个人 ~/.lumen-x，也不要为通过验收关闭 Gatekeeper。签名、公证、Windows、ECS 与真实供应商各自保留验收边界。

## Windows（已有路径，未由本次 macOS 修复验证）

预备 PowerShell 5.1+、Python 3.11+、Node.js 18+、FFmpeg 和 Edge WebView2 Runtime。Windows 脚本仍是既有独立流程，执行前在独占副本检查其依赖安装和清理行为。

```powershell
.\build_windows.ps1
```

```text
dist_windows\LumenX Studio.exe
```

## 运行时数据

默认用户数据在 macOS/Linux 的 ~/.lumen-x/、Windows 的 C:\Users\<username>\.lumen-x\，包含 config.json 和 logs/。验收使用显式 --data-dir / LUMENX_DATA_DIR 指向本任务目录，避免修改个人配置或项目。
