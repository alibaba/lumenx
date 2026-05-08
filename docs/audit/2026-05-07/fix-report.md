# 修复报告：端口统一 / 导出与音频真实化 / 安全边界收紧 / 状态口径收口

## 1. 已修复

- 后端端口统一收敛到 `LUMENX_API_PORT`，桌面壳、Node 启动脚本、Docker、前端默认地址都从同一变量读取。
- 本地启动默认监听改为 `127.0.0.1`，示例环境也收敛到 `LUMENX_API_HOST=127.0.0.1`；仅 Docker 容器内继续保留 `0.0.0.0`。
- 导出接口已从参数占位改为 FFmpeg 实际转码，分辨率、格式、字幕选项会真实生效。
- SFX/BGM 已从 dummy bytes 改为可播放的程序化 `.wav` 生成，并在导出阶段参与混音。
- Next.js 生产静态导出时不再声明开发态 `rewrites`，已消除 `rewrites + output: export` 警告。
- 后端 CORS 已收紧到 loopback origin 规则，`/files` 仅暴露允许的媒体前缀，上传接口增加了大小与类型校验。
- 开发态配置写回已迁出项目根 `.env`，默认写入 `~/.lumen-x/config/runtime.env`。
- 前端首页同步会用后端快照覆盖本地项目列表，并在删除失败时保留本地状态而不是静默回滚。
- 运行时 i18n 已收口为 `zh-CN` 单语言，英文仅保留为历史脚手架，不再作为可选运行时 locale。
- 前端 ESLint warning 已收敛到 0，并把 `frontend/lint-warning-budget.json` 的 `maxWarnings` 收紧为 `0`。
- 前端 API 边界继续收紧：上传资产、项目样式更新、语音列表等接口都有明确返回类型，组件侧不再依赖局部 `any` 兜底。
- 质量门禁文档已同步为当前口径：前端 warning 零容忍，后端最终总门禁恢复为 `pytest -q`。
- CI 已将 PR/`main` 分支门禁收口到 `frontend-quality-gate` 与 `backend-quality-gate` 两个 job。
- 已补充 `docs/audit/2026-05-08/score-review-9.5-target.md`，明确安全边界之外的达标证据与剩余观察项。
- 已补充 fixture 导入、导出转码、系列资产导入三条稳定 smoke 验收路径。

## 2. 关键说明

- 导出仍依赖项目先完成视频合并；若没有可用 `merged_video_url`，接口会先触发现有合并流程。
- 字幕选择 `burn-in` 时会烧录到视频；选择 `srt` 时会生成独立字幕文件并返回 `subtitle_url`。
- 总混页面的轨道排布仍是可视化预览，音量滑块暂未作为导出参数持久化；但 SFX/BGM 文件与导出混音链路已经是真实实现。
- 目前仍保留少量本机回环与 Docker 的双入口差异，属于部署场景差异，不再混用同一个“localhost/8000”叙述。

## 3. 验证结果

- `npx eslint src --format stylish`：0 error / 0 warning
- `npm -C frontend run lint:budget`：`ESLint warning budget: 0/0`
- `npm -C frontend run typecheck`：通过
- `npm -C frontend run test`：145 passed
- `npm -C frontend run build`：通过，无 `rewrites + output: export` 警告
- `pytest -q`：249 passed

## 4. 涉及文件

- `.env.example`
- `Dockerfile.backend`
- `docker-compose.yml`
- `main.py`
- `scripts/start-backend.js`
- `scripts/open-browser.js`
- `scripts/runtime-config.js`
- `start_backend.sh`
- `frontend/next.config.mjs`
- `frontend/src/lib/api.ts`
- `frontend/src/app/page.tsx`
- `frontend/src/components/project/ProjectCard.tsx`
- `frontend/src/store/projectStore.ts`
- `frontend/src/lib/i18n/index.ts`
- `frontend/src/lib/i18n/zh-CN.ts`
- `frontend/src/components/modules/ExportStudio.tsx`
- `frontend/src/components/modules/FinalMixStudio.tsx`
- `src/utils/runtime_config.py`
- `src/apps/comic_gen/api.py`
- `src/apps/comic_gen/audio.py`
- `src/apps/comic_gen/export.py`
- `src/apps/comic_gen/models.py`
- `src/apps/comic_gen/pipeline.py`
- `tests/test_api_security.py`
- `tests/test_env_config_masking.py`
- `tests/test_audio_generation.py`
- `tests/test_export_manager.py`
