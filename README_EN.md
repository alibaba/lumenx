<!-- Banner -->
<div align="center">
  <img src="docs/images/LumenX-Studio-Banner-cybr.png" alt="LumenX" width="100%" />
</div>

<div align="center">

# LumenX

### AI-Native Motion Comic & Video Creation Platform
**Render Noise into Narrative**

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/)
[![GitHub Stars](https://img.shields.io/github/stars/alibaba/lumenx?style=social)](https://github.com/alibaba/lumenx)

[English](README_EN.md) · [中文](README.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

</div>

---

LumenX is an **AI-native motion comic & video creation platform**. It transforms creative text into publishable dynamic videos, providing a complete workflow from script analysis to final export, while also supporting standalone image/video generation.

LumenX currently includes two core modules:

| Module | Purpose |
|--------|---------|
| **LumenX Studio** | Pipeline-first comic/video production (Script → Storyboard → Assets → Video → Export) |
| **LumenX Playground** | Standalone image/video generation workbench (no project context required) |

---

## ✨ Core Capabilities

<table>
<tr>
<td width="50%">

### 🎬 Studio — Full Pipeline Production

- **Deep Script Analysis** — LLM auto-extracts characters/scenes/props, generates structured storyboards
- **Art Direction Control** — Custom visual styles with global consistency
- **Multi-model Asset Generation** — Character turnarounds, scene establishing shots, prop references
- **AI Video Generation** — I2V / R2V multi-mode video generation + batch candidates
- **Smart Dubbing** — CosyVoice / Qwen3-TTS multi-voice dialogue synthesis
- **One-click Export** — Timeline editing + FFmpeg merging

</td>
<td width="50%">

### 🎨 Playground — Standalone Generation Workbench

- **6 Generation Modes** — Image, Text-to-Video, Image-to-Video, Reference-to-Video, Video Editing
- **10+ AI Models** — GPT-Image-2, Wan 2.7, Seedance 2.0, Kling V3, Vidu Q3, HappyHorse, etc.
- **Dynamic Parameters** — Per-model parameter configuration (size/resolution/duration/quality)
- **Concurrent Tasks** — Multiple tasks execute simultaneously with real-time status tracking
- **Prompt Templates** — Save/reuse/favorite/history
- **Gallery View** — Grid/gallery toggle + detail panel

</td>
</tr>
</table>

---

## 🎨 v1.2.1 Visual Identity Refresh

<div align="center">

| Before | After |
|:---:|:---:|
| <img src="docs/images/LumenX Studio Banner.jpeg" alt="Old Banner" width="100%" /> | <img src="docs/images/LumenX-Studio-Banner-cybr.png" alt="New Banner" width="100%" /> |
| Neon gradient lotus · Soft curves | Cyber Brutalism · Angular geometry · Circuit textures |

</div>

---

## 📸 Screenshots

<div align="center">

| Studio Storyboard | Playground |
|:---:|:---:|
| <img src="docs/images/studio-storyboard.jpg" alt="Studio" width="100%" /> | <img src="docs/images/playground-overview.jpg" alt="Playground" width="100%" /> |

</div>

---

## 🎯 Supported AI Models

| Provider | Models | Capabilities |
|----------|--------|--------------|
| **DashScope** | Wan 2.7 Image/Video, Qwen Image 2.0, HappyHorse 1.0 | T2I, I2I, I2V, R2V, T2V, V2V |
| **DashScope** | Kling V3 | I2V, R2V |
| **DashScope** | Vidu Q3 Pro / Turbo | I2V, R2V |
| **DashScope** | PixVerse V6 / C1 | I2V, R2V |
| **MuleRun** | Seedance 2.0 | T2V, I2V, R2V |
| **MuleRun** | GPT-Image-2 | T2I, I2I (up to 4K) |
| **Kling Direct** | Kling V3 | I2V, R2V |
| **Vidu Direct** | Vidu Q3 Pro / Turbo | I2V, R2V |
| **DashScope** | CosyVoice, Qwen3-TTS | TTS Dubbing |
| **DashScope** | Qwen 3.7 Plus | Script Analysis, Prompt Polish |
| **ComfyUI (Local)** | Any local ComfyUI workflow (Wan 2.2 / LTX 2.3 / FishAudio, etc.) | T2I, I2I, I2V, R2V, TTS |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- FFmpeg (for video processing)

### One-command Launch

```bash
# Clone
git clone https://github.com/alibaba/lumenx.git
cd lumenx

# Configure API Key
cp .env.example .env
# Edit .env, fill in DASHSCOPE_API_KEY; or use "ComfyUI All-Local Mode" (see below) — no cloud key needed

# Start (backend on 17177 + frontend on 3008, auto-opens browser)
npm run dev
```

Or start separately:

```bash
# Backend
pip install -r requirements.txt
./start_backend.sh  # http://localhost:17177

# Frontend
cd frontend && npm install && npm run dev  # http://localhost:3008
```

### Access

- **Studio**: http://localhost:3008
- **Playground**: http://localhost:3008/#/playground
- **API Docs**: http://localhost:17177/docs

---

## ⚙️ Configuration Modes

LumenX uses a **local-first** architecture. The minimal setup requires only one API key.

| Mode | Required | Available Capabilities |
|------|----------|----------------------|
| **Basic** | `DASHSCOPE_API_KEY` | Wan/Qwen/HappyHorse/PixVerse/Kling(proxy)/Vidu(proxy) + TTS |
| **+ MuleRun** | + `mulerun login` or `MULEROUTER_API_KEY` | + Seedance 2.0 + GPT-Image-2 |
| **+ Kling Direct** | + `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | Kling direct connection |
| **+ Vidu Direct** | + `VIDU_API_KEY` | Vidu direct connection |
| **+ OSS** | + Alibaba Cloud OSS credentials | Cloud media mirror + signed URLs |
| **All-Local (ComfyUI)** | + `COMFYUI_BASE_URL` (+ local LLM `LLM_BASE_URL`) | Image/video/audio all run on local ComfyUI; LLM uses any OpenAI-compatible local service |

<details>
<summary>Detailed Configuration</summary>

All settings can be configured via:
- **Development**: `.env` file in project root
- **In-app Settings**: Settings page (saves to `~/.lumen-x/config.json`)

MuleRun supports two authentication methods:
1. **CLI mode** (recommended): `npm i -g @mulerunai/cli && mulerun login`
2. **API Key mode**: Enter `muk-...` format key in Settings page

</details>

---

## 🖥️ ComfyUI All-Local Mode

LumenX can switch image / video / audio generation entirely to a local ComfyUI server, while the LLM (script analysis, prompt polishing) uses any OpenAI-compatible local service (Ollama / vLLM / LM Studio, etc.). Cloud providers remain optional. Any model name starting with `comfyui/` or `comfyui-` is automatically routed to the local ComfyUI adapters — no generation-flow changes required.

### Environment Variables

```dotenv
# ComfyUI server
COMFYUI_BASE_URL=http://localhost:8188
COMFYUI_PROTOCOL=standard       # standard=vanilla ComfyUI API (default) / zealman=ZEALMAN control panel
COMFYUI_API_KEY=                # optional, panel auth

# Local LLM (OpenAI-compatible)
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL_NAME=qwen2.5:72b

# Local TTS (optional, ComfyUI FishAudio workflow)
COMFYUI_TTS_ENABLED=1
```

### Workflow Mapping

Feature → ComfyUI workflow ID mappings live in `config/workflow_mapping.json`:

- `asset_generation` / `storyboard` / `video_generation` / `audio_generation`: feature → workflow ID (C16 text-to-image, G03 image-to-video, P02 motion transfer, N2 voice clone, etc.)
- `model_overrides`: specific model ID (e.g. `comfyui-wan2.2-i2v`) → workflow ID
- `node_mapping`: input node fields for the ZEALMAN panel (positive/negative prompt, etc.)

Native ComfyUI (`standard`) is the default: drop an exported workflow JSON into `config/comfyui_workflows/` and point the mapping at its filename. ZEALMAN control panel users can switch by setting `COMFYUI_PROTOCOL=zealman`.

### Built-in Local Models

| Model ID | Capability | Default Workflow |
|----------|-----------|------------------|
| `comfyui-wan2.2-t2i` | Text-to-image | C16 short-drama text-to-image |
| `comfyui-wan2.2-i2i` | Image-to-image | B13 character multi-angle |
| `comfyui-wan2.2-i2v` | Image-to-video | G03 Wan2.2 SmoothMix |
| `comfyui-wan2.2-r2v` | Motion transfer | P02 Wan2.2 Animate |
| `comfyui-ltx2.3-i2v` | Image-to-video | H17 LTX2.3 |

> ComfyUI can run any model you have installed: these IDs are just "feature → workflow" entry labels; the actual checkpoint is decided by the workflow. To use a new model, edit `workflow_mapping.json` or add an entry under `config/model_catalog/families/comfyui.yaml`.

### Switch Defaults to All-Local

Edit `defaults.model_settings` in `config/model_catalog/catalog.meta.yaml`, replacing `t2i_model` / `i2i_model` / `image_model` / `i2v_model` / `r2v_model` with the ComfyUI model IDs above, then run:

```bash
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```

Full reference: [ComfyUI integration reference](docs/1-api-reference/comfyui-workflows.md).

---

## 🏗️ Architecture

<div align="center">
  <img src="docs/images/architecture-cybr.png" alt="LumenX System Architecture" width="90%" />
</div>

### Directory Structure

```
lumenx/
├── frontend/                  # Next.js Frontend
│   └── src/components/
│       ├── modules/playground/   # Playground module
│       ├── modules/              # Studio business modules
│       └── layout/               # Global layout
├── src/
│   ├── apps/comic_gen/        # Studio backend (API + Pipeline)
│   ├── apps/playground/       # Playground backend (API + Service)
│   ├── models/                # AI model adapters (Wanx/Kling/Vidu/MuleRouter/ComfyUI)
│   └── audio/                 # TTS voice synthesis
├── config/model_catalog/      # Model catalog (YAML → JSON)
└── output/                    # Generated outputs (local storage)
```

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [User Manual](USER_MANUAL.md) | Feature usage guide |
| [API Docs](http://localhost:17177/docs) | Swagger UI |
| [Model Onboarding](docs/2-model-catalog-design/model-onboarding-implementation.md) | New model integration guide |
| [Catalog Architecture](docs/2-model-catalog-design/plans/2026-04-03-model-docs-and-catalog-architecture.md) | Model catalog design |
| [Playground PRD](docs/5-playground-roadmap/2026-06-06-playground-standalone-generation-prd.md) | Playground design document |

---

## 🤝 Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md).

- **Bug Reports**: [GitHub Issues](https://github.com/alibaba/lumenx/issues)
- **Feature Requests**: [GitHub Discussions](https://github.com/alibaba/lumenx/discussions)
- **Email**: [zhangjunhe.zjh@alibaba-inc.com](mailto:zhangjunhe.zjh@alibaba-inc.com)

---

## 📄 License

[MIT License](LICENSE)

---

<div align="center">
  Made with ❤️ by StarLotus · Alibaba Group
</div>
