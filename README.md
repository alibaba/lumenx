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

LumenX 是一个 **AI 原生的短漫剧 & 视频创作平台**。它将创意文本转化为可发布的动态视频，提供从剧本分析到成片导出的完整创作链路，同时支持独立的图像/视频生成能力。

LumenX 目前包含两个核心模块：

| 模块 | 定位 |
|------|------|
| **LumenX Studio** | Pipeline-first 漫剧/视频生产（剧本→分镜→资产→视频→合成→导出） |
| **LumenX Playground** | 独立图像/视频生成工具台（无需剧本上下文，即开即用） |

---

## ✨ 核心能力

<table>
<tr>
<td width="50%">

### 🎬 Studio — 全链路漫剧生产

- **深度剧本分析** — LLM 自动提取角色/场景/道具，生成结构化分镜脚本
- **可控美术指导** — 自定义视觉风格，全片画风统一
- **多模型资产生成** — 角色三视图、场景定调图、道具参考图
- **AI 分镜视频** — I2V / R2V 多模式视频生成 + 批量抽卡
- **智能配音** — CosyVoice / Qwen3-TTS 多音色对白合成
- **一键合成导出** — 时间线编辑 + FFmpeg 拼接成片

</td>
<td width="50%">

### 🎨 Playground — 独立生成工具台

- **6 种生成模式** — 图像生成、文生视频、图生视频、参考生视频、视频编辑
- **10+ AI 模型** — GPT-Image-2、Wan 2.7、Seedance 2.0、Kling V3、Vidu Q3、HappyHorse 等
- **动态参数** — 每个模型独立参数（尺寸/分辨率/时长/画质）
- **并发任务** — 多任务同时执行，实时状态追踪
- **Prompt 模板** — 收藏/复用/历史记录
- **画廊视图** — 网格/画廊切换 + 详情面板

</td>
</tr>
</table>

---

## 🎨 v1.2.1 主视觉焕新

<div align="center">

| Before | After |
|:---:|:---:|
| <img src="docs/images/LumenX Studio Banner.jpeg" alt="旧版 Banner" width="100%" /> | <img src="docs/images/LumenX-Studio-Banner-cybr.png" alt="新版 Banner" width="100%" /> |
| 霓虹渐变莲花 · 柔和曲线 | Cyber Brutalism · 棱角几何 · 电路纹理 |

</div>

---

## 📸 产品截图

<div align="center">

| Studio 分镜工作台 | Playground 创作台 |
|:---:|:---:|
| <img src="docs/images/studio-storyboard.jpg" alt="Studio" width="100%" /> | <img src="docs/images/playground-overview.jpg" alt="Playground" width="100%" /> |

</div>

---

## 🎯 支持的 AI 模型

| Provider | 模型 | 能力 |
|----------|------|------|
| **DashScope** | Wan 2.7 Image/Video, Qwen Image 2.0, HappyHorse 1.0 | T2I, I2I, I2V, R2V, T2V, V2V |
| **DashScope** | Kling V3 | I2V, R2V |
| **DashScope** | Vidu Q3 Pro / Turbo | I2V, R2V |
| **DashScope** | PixVerse V6 / C1 | I2V, R2V |
| **MuleRun** | Seedance 2.0 | T2V, I2V, R2V |
| **MuleRun** | GPT-Image-2 | T2I, I2I (含 4K) |
| **Kling 原厂** | Kling V3 | I2V, R2V |
| **Vidu 原厂** | Vidu Q3 Pro / Turbo | I2V, R2V |
| **DashScope** | CosyVoice, Qwen3-TTS | TTS 配音 |
| **DashScope** | Qwen 3.7 Plus | 剧本分析、Prompt 润色 |

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- FFmpeg（视频处理）

### 一键启动

```bash
# 克隆
git clone https://github.com/alibaba/lumenx.git
cd lumenx

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY（必填）

# 启动（后端 17177 + 前端 3008，自动开浏览器）
npm run dev
```

或分别启动：

```bash
# 后端
pip install -r requirements.txt
./start_backend.sh  # http://localhost:17177

# 前端
cd frontend && npm install && npm run dev  # http://localhost:3008
```

### 访问

- **Studio**: http://localhost:3008
- **Playground 创作台**: http://localhost:3008/#/playground
- **API Docs**: http://localhost:17177/docs

---

## ⚙️ 配置模式

LumenX 采用 **本地优先** 的架构，最简配置只需一个 API Key。

| 模式 | 必填 | 可用能力 |
|------|------|----------|
| **基础** | `DASHSCOPE_API_KEY` | Wan/Qwen/HappyHorse/PixVerse/Kling(代理)/Vidu(代理) + TTS |
| **+ MuleRun** | + `mulerun login` 或 `MULEROUTER_API_KEY` | + Seedance 2.0 + GPT-Image-2 |
| **+ Kling 原厂** | + `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | Kling 直连 |
| **+ Vidu 原厂** | + `VIDU_API_KEY` | Vidu 直连 |
| **+ OSS** | + 阿里云 OSS 凭证 | 云端媒体镜像 + 签名 URL |

<details>
<summary>详细配置说明</summary>

所有配置可通过以下方式设置：
- **开发模式**: 项目根目录 `.env` 文件
- **应用内设置**: Settings 页面（保存到 `~/.lumen-x/config.json`）

MuleRun 支持两种认证方式：
1. **CLI 模式**（推荐）: `npm i -g @mulerunai/cli && mulerun login`
2. **API Key 模式**: 在设置页填入 `muk-...` 格式的 Key

</details>

---

## 🏗️ 技术架构

<div align="center">
  <img src="docs/images/architecture-cybr.png" alt="LumenX System Architecture" width="90%" />
</div>

### 目录结构

```
lumenx/
├── frontend/                  # Next.js 前端
│   └── src/components/
│       ├── modules/playground/   # Playground 创作台
│       ├── modules/              # Studio 业务模块
│       └── layout/               # 全局布局
├── src/
│   ├── apps/comic_gen/        # Studio 后端 (API + Pipeline)
│   ├── apps/playground/       # Playground 后端 (API + Service)
│   ├── models/                # AI 模型适配器 (Wanx/Kling/Vidu/MuleRouter)
│   └── audio/                 # TTS 语音合成
├── config/model_catalog/      # 模型目录 (YAML → JSON)
└── output/                    # 生成产物 (本地存储)
```

---

## 📖 文档

| 文档 | 说明 |
|------|------|
| [用户手册](USER_MANUAL.md) | 功能使用说明 |
| [API 文档](http://localhost:17177/docs) | Swagger UI |
| [模型接入](docs/2-model-catalog-design/model-onboarding-implementation.md) | 新模型接入指南 |
| [Catalog 架构](docs/2-model-catalog-design/plans/2026-04-03-model-docs-and-catalog-architecture.md) | 模型目录设计 |
| [Playground PRD](docs/5-playground-roadmap/2026-06-06-playground-standalone-generation-prd.md) | 创作台设计文档 |

---

## 🤝 参与贡献

欢迎社区贡献！请先阅读 [贡献指南](CONTRIBUTING.md)。

- **Bug 反馈**: [GitHub Issues](https://github.com/alibaba/lumenx/issues)
- **功能建议**: [GitHub Discussions](https://github.com/alibaba/lumenx/discussions)
- **邮件联系**: [zhangjunhe.zjh@alibaba-inc.com](mailto:zhangjunhe.zjh@alibaba-inc.com)

---

## 📄 License

[MIT License](LICENSE)

---

<div align="center">
  Made with ❤️ by StarLotus · Alibaba Group
</div>
