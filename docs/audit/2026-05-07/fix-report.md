# 修复报告：端口统一 / 导出与音频真实化 / 本地监听收紧

## 1. 已修复

- 统一本地开发与文档端口到 `18177`。
- 本地启动默认监听改为 `127.0.0.1`，示例环境也收敛到 `127.0.0.1:18177`；仅 Docker 容器内继续保留 `0.0.0.0`。
- 导出接口已从参数占位改为 FFmpeg 实际转码，分辨率、格式、字幕选项会真实生效。
- SFX/BGM 已从 dummy bytes 改为可播放的程序化 `.wav` 生成，并在导出阶段参与混音。
- Next.js 生产静态导出时不再声明开发态 `rewrites`，已消除 `rewrites + output: export` 警告。

## 2. 关键说明

- 导出仍依赖项目先完成视频合并；若没有可用 `merged_video_url`，接口会先触发现有合并流程。
- 字幕选择 `burn-in` 时会烧录到视频；选择 `srt` 时会生成独立字幕文件并返回 `subtitle_url`。
- 总混页面的轨道排布仍是可视化预览，音量滑块暂未作为导出参数持久化；但 SFX/BGM 文件与导出混音链路已经是真实实现。

## 3. 验证结果

- `pytest -q`：227 passed
- `npm -C frontend run test -- --runInBand`：142 passed
- `npm -C frontend run build`：通过，无 `rewrites + output: export` 警告

## 4. 涉及文件

- `.env.example`
- `scripts/start-backend.js`
- `scripts/open-browser.js`
- `start_backend.sh`
- `main.py`
- `docker/nginx.conf`
- `frontend/next.config.mjs`
- `frontend/src/lib/api.ts`
- `frontend/src/components/modules/ExportStudio.tsx`
- `frontend/src/components/modules/FinalMixStudio.tsx`
- `frontend/src/lib/i18n/zh-CN.ts`
- `README.md`
- `README_EN.md`
- `src/apps/comic_gen/api.py`
- `src/apps/comic_gen/audio.py`
- `src/apps/comic_gen/export.py`
- `src/apps/comic_gen/models.py`
- `src/apps/comic_gen/pipeline.py`
- `tests/test_audio_generation.py`
- `tests/test_export_manager.py`
