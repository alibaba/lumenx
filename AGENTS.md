# AGENTS.md

This file provides guidance to AI coding agents (Codex CLI / Qoder) when working with code in this repository. It mirrors the project's `CLAUDE.md` so that Claude Code, Codex, and Qoder share identical project rules. When this file and `CLAUDE.md` diverge, treat `CLAUDE.md` as the source of truth and re-sync here.

## Git Commit Rules

- Git author is already configured for this repo, do not modify git config
- **NEVER** add `Co-Authored-By` lines in commit messages
- Push to GitHub remote (`github`) only, ignore `origin` (deprecated GitLab)
- **Atomic commits** — 每完成一个独立功能点就立即 commit，不要攒到最后一次性大提交。拆分粒度示例：后端模块 → commit、前端骨架 → commit、UI 组件 → commit、catalog 变更 → commit、bug fix → 单独 commit

## Project Workflow Triggers

When the user asks to do any of the following in this repository:

- publish to the LumenX GitHub mirror
- run the LumenX GitHub publish workflow
- follow the LumenX GitHub release or PR flow
- prepare a GitHub-safe branch, commit, push, or PR for LumenX
- use `/lumenx-git-publish`

Treat that as a request to load and follow:

`.codex/workflows/lumenx-git-publish.md`

When the user asks to do any of the following in this repository:

- onboard a new model into LumenX
- update model docs, model versions, defaults, or parameters
- refresh Wan / Kling / Vidu / PixVerse model support
- run the LumenX model onboarding workflow
- review whether a model change is catalog-only or also needs runtime / UI work
- use `/lumenx-model-onboarding`

Treat that as a request to load and follow:

`.codex/workflows/lumenx-model-onboarding.md`

When the user asks to do any of the following in this repository:

- build the LumenX desktop app
- package LumenX Studio for macOS or Windows
- create a DMG or EXE build
- run the LumenX desktop build workflow
- use `/lumenx-build`

Treat that as a request to load and follow:

`.codex/workflows/lumenx-build.md`

This repository does not rely on native slash commands in Codex. The strings `/lumenx-git-publish`, `/lumenx-build`, and `/lumenx-model-onboarding` are textual aliases for the workflows above.

## Workflow Files

- `.claude/commands/lumenx-git-publish.md` remains the Claude project command source.
- `.claude/commands/lumenx-build.md` remains the Claude project command source.
- `.claude/commands/lumenx-model-onboarding.md` remains the Claude project command source.
- `.codex/workflows/lumenx-git-publish.md` is the Codex workflow mirror for the same project process.
- `.codex/workflows/lumenx-build.md` is the Codex workflow mirror for the desktop build process.
- `.codex/workflows/lumenx-model-onboarding.md` is the Codex workflow mirror for model onboarding, catalog updates, and verification.

If both Claude and Codex guidance exist, preserve behavior parity unless the user asks for divergence.

After editing any file in `.claude/commands/` or `.codex/workflows/`, run `python3 scripts/check_workflow_parity.py` to verify mirror parity. Record intentional divergences with reasons in the script's `WAIVERS` table.

# LumenX Product Family (Core + Studio + Atelier)

## Overview

> **重要：项目已从单一产品演进为产品家族。** 旧记忆中"LumenX Studio = AI Comic Generator"的认知已过时。

LumenX 现在是一个 **产品家族**，由 Codex 主导推进了重要的架构演进：

```text
LumenX Core              # 共享后端/运行时/API capability
├── LumenX Studio        # Pipeline-first 漫剧/视频生产产品（原 Comic Generator）
└── LumenX Atelier       # Graph-first 个人创作无限画布产品（代码在独立分支开发中）
```

- **LumenX Studio**：保持 pipeline-first（项目 → 剧本 → 分镜 → 资产 → R2V/I2V → 合成 → 导出）。面向工作室、团队、系列号。当前 Phase 1 重点：R2V workflow 稳定化。
- **LumenX Atelier**：全新 graph-first 创作壳，面向个人创作者。"Seed → Plan → Draft Nodes → Generation → Takes → Judgment → Branches → Sequence → Export"。Agent 可在画布上提议、生成、变体探索。Atelier 代码在独立分支开发中（如 `feat/atelier-v4-canvas-uplift`），尚未合入 main。
- **LumenX Core**：共享 model catalog、provider routing、media、generation jobs、export 等原语。Studio 与 Atelier 不共享前端状态，只共享 Core capability。

技术栈：Next.js 14 前端 + FastAPI 后端，集成阿里云 DashScope/Qwen/Wanx、Kling、Vidu、PixVerse、HappyHorse、MuleRouter（Seedance/GPT-Image-2）等 provider。

## Architecture

### Frontend
- Framework: Next.js 14 + React 18 + TypeScript + Tailwind CSS
- State management: Zustand
- HTTP client: Axios
- 3D rendering: Three.js + @react-three/fiber
- Animation: Framer Motion

### Backend
- Framework: FastAPI (Python 3.11+)
- AI integration: Alibaba Cloud Qwen/Wanx services via DashScope
- Data validation: Pydantic
- File storage: Local + Alibaba Cloud OSS

### Core Components

#### Frontend Structure（Studio，main 分支）
```
frontend/
├── src/app/page.tsx              # 路由总入口
├── src/components/
│   ├── layout/                   # 通用布局
│   ├── modules/                  # Studio 业务模块
│   │   ├── ScriptInput/          # 剧本输入
│   │   ├── ArtDirection/         # 美术风格
│   │   ├── cast/                 # 配音工作台（CastWorkbenchModal、VoiceClone/Design/Picker）
│   │   └── storyboard-r2v/      # R2V 分镜（ShotCard、PolishPanel、VideoConfigModal 等 17+ 文件）
│   ├── shared/                   # 跨模块共享组件
│   │   ├── BorderGlow/           # 边框发光效果
│   │   ├── StepHeader.tsx        # 统一步骤标题
│   │   ├── ToastContainer.tsx    # Toast 通知
│   │   └── preview/              # LightboxProvider、PreviewImage、PreviewVideo
│   ├── canvas/                   # Studio 画布
│   └── project/                  # Studio 项目相关组件
├── src/store/
│   ├── projectStore.ts           # Studio 状态
│   ├── settingsStore.ts          # 设置状态
│   └── toastStore.ts             # Toast 通知状态
├── src/lib/
│   ├── api.ts                    # API 客户端
│   ├── modelCatalog.ts           # 模型目录查询
│   ├── i18n.ts                   # 国际化
│   ├── debugLog.ts               # 调试日志
│   └── utils.ts                  # 通用工具
└── src/__tests__/                # 测试
```

#### Frontend Structure（Atelier，独立分支）
```
frontend/
├── src/app/page.tsx              # hash #/atelier 切换到 Atelier shell
├── src/components/
│   └── atelier/                  # ★ LumenX Atelier 产品壳（独立，不允许引入 Studio 模块）
│       ├── AtelierShell.tsx      # 全屏画布 + Agent 面板 + Sequence strip
│       └── AgentPanelTrace.tsx   # Agent 历史/会话/规划态视图
├── src/store/
│   └── atelierStore.ts           # ★ Atelier 独立状态（projects/turns/pendingApproval/...）
└── src/lib/
    ├── api.ts                    # 包含 /atelier/* 客户端方法和类型
    ├── atelierCanvas.ts          # 画布几何/节点关系工具
    └── atelierAgentPlanning.ts   # Planner 包/会话校验逻辑
```

> **硬约束**：`components/atelier/` 严禁 import Studio 模块组件；反之亦然。共享的视觉/逻辑要先提升到中性层（未来 `packages/*`）。

#### Backend Structure
```
src/
├── apps/comic_gen/                 # 当前 Studio 域（Atelier 域在独立分支临时同居）
│   ├── api.py                      # FastAPI 路由（含 /projects/*；Atelier 分支还含 /atelier/*）
│   ├── pipeline.py                 # 业务流程编排（含 Studio 持久化；Atelier 分支含 Atelier 持久化）
│   ├── models.py                   # Pydantic 数据模型（Atelier 分支含 AtelierProject/Node/Agent*）
│   ├── atelier_agent.py            # ★ Atelier Agent 运行时（独立分支；Tool/Planner/Permission/Harness）
│   ├── llm.py                      # LLM 交互（剧本分析、prompt polish、PolishError）
│   ├── llm_adapter.py              # DashScope OpenAI 兼容封装
│   ├── prompt_assembly.py          # Prompt 组装
│   ├── assets.py / storyboard.py / video.py / audio.py / export.py
│   └── test_pipeline.py
├── models/                         # AI 模型 wrapper
│   ├── factory.py                  # 模型路由工厂（model name → adapter）
│   ├── base.py                     # 基类
│   ├── kling.py                    # Kling 视频模型
│   ├── vidu.py                     # Vidu 视频模型
│   ├── wanx.py                     # 通义万相图像模型
│   ├── qwen_vl.py                  # Qwen VL 多模态（OpenAI 兼容接口）
│   ├── mulerouter.py               # ★ MuleRouter/MuleRun 适配器（Seedance 2.0 视频 + GPT-Image-2 图像）
│   └── image.py                    # 图像生成通用逻辑
├── audio/
│   └── tts.py                      # CosyVoice TTS（voice ID 自动匹配 model 版本）
├── utils/                          # 工具（OSS 等）
└── config.py
```

> **未来拆分计划**：Atelier 域将迁出到 `src/apps/atelier/`，前端 shell 迁到 `frontend/src/app/atelier/`，共享客户端到 `packages/lumenx-core-client/`。当前在同一仓库内仅作为 Atelier-domain APIs 存在，不可让 Studio 状态成为 Atelier canvas 状态的父级。

## Development Commands

### Unified Development
```bash
# Install root dev dependencies when needed
npm install

# Runs predev setup, starts backend on 17177, frontend on 3008, then opens the browser
npm run dev
```

`npm run dev` runs `scripts/dev-setup.js` first. It attempts to create `.venv`, install local Python/frontend dependencies when missing, and then start both services. If the editable Python install path fails on a clean checkout, use the backend setup command below with `pip install -r requirements.txt`.

### Initial Setup
```bash
# Copy environment template
cp .env.example .env
# Edit .env and add your Alibaba Cloud API keys
```

### Backend Development
```bash
# Install dependencies
pip install -r requirements.txt

# Create output directories
mkdir -p output/uploads

# Start backend server
./start_backend.sh
# or
python -m uvicorn src.apps.comic_gen.api:app --reload --host 0.0.0.0 --port 17177

# API docs available at: http://localhost:17177/docs
```

### Frontend Development
```bash
cd frontend
npm install
npm run dev
# Frontend available at: http://localhost:3008
```

### Verification Commands
Keep in sync with the same section in `CLAUDE.md`.

Frontend verification commands:
```bash
cd frontend
npm run typecheck
npm run test
npm run test:ui
npm run test:all
npm run build
```

Backend/catalog verification commands:
```bash
pytest -q
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

### Full Development Mode
```bash
# Terminal 1: Start backend
./start_backend.sh

# Terminal 2: Start frontend
cd frontend && npm run dev
```

### Desktop App Mode
```bash
# Run the complete desktop application
python main.py
```

### Model Catalog Workflow
For model onboarding, version/default updates, provider capability changes, or UI model exposure changes, load `.codex/workflows/lumenx-model-onboarding.md` before editing.

The executable catalog source lives under `config/model_catalog/`. After catalog YAML changes, regenerate and validate:
```bash
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

The generated artifacts are intentionally committed:
- `config/model_catalog/generated/model_catalog.json`
- `frontend/src/generated/modelCatalog.json`
- `config/model_catalog/schema/model-catalog.schema.json`

## File Structure

### Output Management
Generated files are stored in `output/`:
```
output/
├── assets/              # Character/scene/prop images
│   ├── characters/      # Character artwork
│   ├── scenes/          # Scene backgrounds
│   └── props/           # Prop items
├── audio/               # Generated narration/dialogue audio
├── export/              # Export intermediates
├── storyboard/          # Storyboard renders
├── video/               # Final merged videos
├── uploads/             # User-uploaded files
└── video_inputs/        # Video generation source images
```

### Project Data
Development project data is stored in this repository under `output/`:
- `output/projects.json` - Studio main project database
- `output/series.json` - Studio series database
- `output/atelier_projects.json` - **★ Atelier 画布/节点/Agent turn 持久化（独立于 Studio）**
- generated media under `output/assets/`, `output/storyboard/`, `output/video/`, `output/audio/`, and `output/uploads/`

Packaged desktop app configuration and logs are stored under `~/.lumen-x/`:
- `~/.lumen-x/config.json` - App settings, API keys, and OSS configuration
- `~/.lumen-x/logs/app.log` - Desktop app log file

## Key API Endpoints

### Project Management
- `POST /projects` - Create new project from script text
- `GET /projects` - List all projects
- `GET /projects/{id}` - Get project details
- `DELETE /projects/{id}` - Delete project
- `PUT /projects/{id}/reparse` - Reprocess script for project

### Asset Generation
- `POST /projects/{id}/generate_assets` - Generate all project assets
- `POST /projects/{id}/assets/generate` - Generate specific asset
- `POST /projects/{id}/assets/toggle_lock` - Lock/unlock asset
- `POST /projects/{id}/assets/update_image` - Update asset image

### Storyboard & Video
- `POST /projects/{id}/generate_storyboard` - Generate storyboards
- `POST /projects/{id}/storyboard/render` - Render specific frame
- `POST /projects/{id}/generate_video` - Generate videos from storyboards
- `POST /projects/{id}/video_tasks` - Create video generation tasks
- `POST /projects/{id}/merge` - Merge video segments

### Art Direction
- `POST /projects/{id}/art_direction/analyze` - Analyze script for style
- `POST /projects/{id}/art_direction/save` - Save art direction
- `GET /art_direction/presets` - Get style presets

### LumenX Atelier（独立分支，合入后可用）
画布/节点 CRUD：
- `POST /atelier/projects` / `GET /atelier/projects` / `GET|PUT|DELETE /atelier/projects/{id}`
- `POST|PUT|DELETE /atelier/projects/{id}/nodes[/{node_id}]`
- `POST /atelier/projects/{id}/nodes/{node_id}/video_candidates[/select|/regenerate|/{cid}/retry]`

Agent runtime（Codex 风格 approval + 独立 planner）：
- `PUT /atelier/projects/{id}/agent_policy` — 设置 approval_mode (`untrusted`/`on_failure`/`on_request`/`never`)、`allowed_tools`、`max_nodes_per_action`
- `GET /atelier/projects/{id}/agent/tools` — 列出可用工具规格
- `POST /atelier/projects/{id}/agent/planner_package` — 构建可送给规划器的只读 context 包
- `POST /atelier/projects/{id}/agent/plan` — 规划器返回结构化 tool_calls（不执行）
- `POST /atelier/projects/{id}/agent/turns` — 执行/预览 Agent turn；遵循 policy，触发 approval/denial/exec

## Atelier 关键设计与硬约束

> Atelier 代码在独立分支开发中（如 `feat/atelier-v4-canvas-uplift`）。以下规格描述的是已实现或正在实现的架构，合入 main 后即在主线可用。

参考文档（必读）：
- `docs/plans/2026-05-08-lumenx-studio-atelier-core-roadmap.md` — Core/Studio/Atelier 产品家族 roadmap
- `docs/plans/2026-05-08-atelier-v1-implementation-boundary.md` — Atelier v1 边界与任务清单
- `docs/plans/2026-05-09-atelier-agent-runtime-implementation-plan.md` — Agent 运行时实现计划

### 数据模型（src/apps/comic_gen/models.py）
- `AtelierProject` — id/title/source_project_id?/agent_policy/nodes[]/agent_turns[]
- `AtelierNode` — type(`seed|plan|draft|image|video|audio|sequence`)/prompt/status/x,y/w,h/共享引用 (`source_project_id|frame_id|asset_id|video_task_id|media_urls[]`)/data
- `AtelierAgentPolicy` — approval_mode + allowed_tools + max_nodes_per_action
- `AtelierAgentTurn` / `AtelierAgentToolCall` / `AtelierAgentPlan` / `AtelierAgentPlannerPackage`
- `AtelierApprovalMode` 枚举：`UNTRUSTED|ON_FAILURE|ON_REQUEST|NEVER`（默认 UNTRUSTED）

### Agent Runtime 架构（src/apps/comic_gen/atelier_agent.py）
- `AtelierToolRegistry` — 命名空间化工具（`canvas.*` / `generation.*`），含 schema/permission/cost
- `AtelierPlannerRegistry` — 多规划器：`DeterministicCorePlanner`（默认/可重放）+ `ModelAdapterPlanner`（LLM-backed，约束输出）
- `AtelierPermissionEnforcer` — 模式无关的硬规则：未知工具、未授权工具、超过 max_nodes、缺少必需引用、模型/引用不匹配 一律拒绝
- `AtelierAgentHarness` — preview/execute 两阶段；持久化每次 tool call 与 result_snapshot
- 默认上限：每 turn ≤ 8 个 tool 调用，≤ 1 个生成调用
- v1 工具：`canvas.readProject` / `canvas.createVideoNode` / `canvas.updateNodePrompt` / `canvas.createReferenceImageNode` / `canvas.attachReferenceNode` / `generation.createVideoCandidates`
- **v1 不暴露 `candidate.select`** — 候选选择属于用户审美判断

### 边界硬约束
- Atelier 不依赖 Studio project；可以无 Studio 项目独立运行
- Atelier Node 只能引用 Studio/Core 共享 artifact（id + media_urls），不可嵌入完整 Studio 快照
- Agent v1 不能：执行任意代码、调用未注册工具、直接改 Studio 项目、默认权限下静默提交昂贵生成、绕过 canvas API
- 默认 approval mode 为 `untrusted`，直到 UI 把成本/风险呈现清楚

### 画布技术
- DOM-node 架构（不是 Canvas2D / WebGL / tldraw）。理由：video 节点需要原生 `<video>`、表单、菜单、进度等
- 自实现轻量 transform 引擎；如交互/边/键盘需求快速增长，再迁移到 `@xyflow/react`

## Key Technical Decisions

- **DashScope 必须用 OpenAI 兼容接口**: `qwen3.6-plus` 等新模型不支持旧 `dashscope.Generation.call()` SDK，只能通过 `https://dashscope.aliyuncs.com/compatible-mode/v1` 调用
- **LLM Adapter**: `src/apps/comic_gen/llm_adapter.py` 统一封装，DashScope 和第三方 OpenAI API 都走 openai 库；DashScope 默认模型走 fallback chain `["qwen3.6-plus", "qwen-plus"]`
- **QwenVL 也用 OpenAI 兼容接口**: `qwen3.6-plus` 同时支持文本和多模态(VL)
- **MuleRouter**: `src/models/mulerouter.py` 是 Seedance 2.0（视频）和 GPT-Image-2（图像）的适配器，支持 MuleRun CLI 模式和 MuleRouter HTTP API 模式。`factory.py` 按 model name 路由到对应适配器
- **CosyVoice TTS**: voice ID 必须匹配 model 版本（`_v2` → `cosyvoice-v2`，`_v3` → `cosyvoice-v3-flash`），通过 `_resolve_model_for_voice()` 自动匹配
- **PolishError 契约**: `polish_*_prompt` 函数抛 `PolishError` → HTTP 502 + reason 码；默认模型 qwen3.6-plus

## Development Guidelines

### Backend Changes
- Update Pydantic models in `src/apps/comic_gen/models.py` when modifying data structures
- Add new endpoints to `src/apps/comic_gen/api.py` using FastAPI conventions
- Implement business logic in appropriate modules in `pipeline.py`
- Use background tasks for AI processing operations
- Keep local-first media handling intact: generated/uploaded files should resolve through managed `output/` paths, with OSS as an optional mirror/signing layer rather than a hard dependency.

### Frontend Changes
- Add new API calls to `frontend/src/lib/api.ts`
- Studio 业务模块放 `frontend/src/components/modules/`；**Atelier 业务模块放 `frontend/src/components/atelier/`**，两者互不 import
- 跨模块共享组件放 `frontend/src/components/shared/`
- Use Zustand stores for shared state management（Studio 用 `projectStore`，Atelier 用 `atelierStore`，**不要混用**）
- Use generated catalog data through `frontend/src/lib/modelCatalog.ts`; do not parse catalog YAML directly in the frontend.
- Atelier UI 的 transient canvas state（pan/zoom/拖拽）保留在前端；node 数据变化必须通过 API 持久化，不可只 local mutation

### Configuration
- API keys can be configured via `.env` file or app settings dialog
- OSS configuration is optional but recommended for cloud storage
- Model settings can be changed per project via `update_model_settings`
- In development, `.env` is read from the project root. In packaged mode, `~/.lumen-x/config.json` is used.

### Goal-Driven Execution

Non-trivial tasks should be framed as verifiable goals with success criteria, not vague instructions.

| 模糊指令 | 转化为可验证目标 |
|---|---|
| "加验证" | 先写 invalid input 测试，再让测试通过 |
| "修这个 bug" | 先写 reproducing test，再让测试通过 |
| "重构 X" | 确认重构前后测试全部通过 |

Multi-step tasks should pair each step with a verification check:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria enable autonomous looping; vague criteria like "make it work" require constant clarification.

## Debugging

### Common Issues
- FFmpeg not found: Install FFmpeg and ensure it's in PATH
- API keys missing: Configure via app settings or .env file
- OSS errors: Verify credentials and bucket permissions
- Video merge failures: Check if video files exist and have proper paths

### Logs
- Backend logs appear in terminal when running start_backend.sh
- Desktop app logs saved to: `~/.lumen-x/logs/app.log`

## Deployment
- Frontend: Built with Next.js, can be deployed as static files
- Backend: Deploy with FastAPI server (Gunicorn recommended for production)
- Desktop app: Built with PyInstaller and pywebview

## Design Context

### Users
Primary: independent creators (self-media, short-video makers) who need to turn text scripts into comic-style videos quickly. Secondary: professional teams using it as a pre-production tool. Both share a need for speed and creative control — they think in stories, not in software.

### Brand Personality
**Creative · Immersive · Geeky** — LumenX feels like a creator's cockpit, not an admin panel. It respects the user's craft while putting AI power at their fingertips. The tagline "Render Noise into Narrative" captures the mission: raw ideas in, polished stories out.

### Aesthetic Direction
- **Dark-first**: Deep space black (#050508) background, no light mode. The darkness lets content (images, videos, storyboards) be the hero.
- **Glassmorphism**: Frosted glass panels (5% white + backdrop-blur) for structure. Layered transparency creates depth without clutter.
- **Neon accents**: Electric blue (#646cff) primary, hot pink (#ff0080) accent. Used sparingly for interactive elements and emphasis — not decoration.
- **Brand gradient**: Purple → Indigo → Pink (the "X" in LumenX). Reserved for branding moments, not sprinkled everywhere.
- **Typography**: Space Grotesk (display/headings — geometric, modern), Inter (body — clean, readable), JetBrains Mono (code/technical values).
- **Anti-references**: No dense tables/forms that feel like enterprise admin. No excessive particles/animations that distract from content. No multi-panel professional tool complexity (not Figma/Photoshop).

### Design Principles

1. **Content is king**: The user's creations (scripts, storyboards, videos, assets) should always be the visual focus. UI chrome stays quiet until needed.
2. **Progressive disclosure**: Show only what matters at each step. Advanced settings (prompt config, model settings) are accessible but not in-your-face. Use collapsible sections and contextual reveals.
3. **Confidence through feedback**: Every action should have clear, immediate visual feedback — loading states, success confirmations, smooth transitions. The user should always know what's happening and feel in control.
4. **Consistent glass language**: All containers use the glass-panel pattern. Inputs use glass-input. Buttons use glass-button or primary fills. No mixing of visual metaphors.
5. **Purposeful motion**: Framer Motion for meaningful transitions (enter/exit, state changes). Staggered reveals for lists. No gratuitous animation — every movement communicates something.

## Codex 协作约定（Atelier 主开发者是 Codex）

> **Atelier 当前主要由 Codex 主导推进**（独立分支）。Claude 介入前先 review 最新进展。

### Agent 协作工件 CDTR 组织规范（来自 AGENTS.md）
新建的非源码协作工件请放入 `docs/agents/`：
- `docs/agents/context/` — 用户输入、抓取的参考资料、需求、截图
- `docs/agents/deliverables/` — 最终面向用户的产出（保持精简）
- `docs/agents/raw/` — 草稿、中间分析、实验、scratch 笔记
- `docs/agents/tools/` — 为 agent workflow 写的复用脚本

CDTR 仅用于 agent 协作工件；**不可** 把 `src/`、`frontend/`、`config/`、`scripts/`、`tests/`、`.codex/workflows/`、运行时数据移入或复制到 CDTR 文件夹。

### Codex / Claude 命令对偶
- `.claude/commands/lumenx-*.md` 与 `.codex/workflows/lumenx-*.md` 是同一流程在两个 agent 上的镜像
- 修改任一边时，除非用户明确要求分叉，否则保持行为对等
