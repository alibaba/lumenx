<!-- Banner Placeholder -->
<div align="center">
  <img src="docs/images/LumenX Studio Banner.jpeg" alt="LumenX Studio Banner" width="100%" />
</div>

<div align="center">

# LumenX Studio

### AI 原生短漫剧创作平台
**Render Noise into Narrative**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/)
[![GitHub Stars](https://img.shields.io/github/stars/alibaba/lumenx?style=social)](https://github.com/alibaba/lumenx)

[English](README_EN.md) | [中文](README.md) | [用户手册](USER_MANUAL.md) | [贡献指南](CONTRIBUTING.md)

</div>

---

LumenX Studio 是一个**AI 短漫剧一站式生产平台**。它能够将小说文本转化为动态视频，打通了从剧本分析、角色定制、分镜绘制到视频合成的完整创作链路。

LumenX Studio将 **资产提取—>风格定调—>资产生成—>分镜脚本构造->分镜图生成—>分镜视频生成** 的全链路SOP天然与该平台功能集成，在提供完善功能的基础上融入短漫剧行业Know-How，方便大家快速制作出质量过关的AI短片，大大提升生产效率。

本平台天然集成 阿里的Qwen & Wanx 系列模型能力，致力于提供智能便捷、灵活可控的漫剧制作体验，无需频繁切换网页或App，使创作者能够一站式地完成短漫剧制作。

---

## ✨ 核心亮点

| 能力 | 描述 |
|------|------|
| 📝 **深度剧本分析** | 基于 LLM 自动提取角色、场景、道具，生成专业分镜脚本 |
| 🎨 **可控美术指导** | 支持自定义视觉风格，保持全片画风统一 |
| 🎬 **可视化分镜** | 拖拽式分镜编辑器，所见即所得地组合人物、背景与特效 |
| 🎥 **多模态生成** | 集成通义万相 (Wanx) 等模型，支持文生图、图生视频 |
| 🎵 **智能视听合成** | 自动生成角色配音 (TTS)、音效 (SFX) 并合成最终视频 |

---

## 📸 产品演示

<div align="center">
  Step 1: Script 剧本编辑与实体提取
  
  画面左侧置有剧本编辑器，剧本编辑后，可点击上方的“提取实体”按钮，由Qwen-Plus自动提取脚本中所涉及到的角色、场景、道具；右侧可针对这些实体进行编辑调整。
  <img src="docs/images/Script_example.jpg" alt="LumenX Studio Script" width="100%" />

  Step 2: Art Direction 风格定调

  可利用Qwen-Plus分析当前剧本适合的风格，也可使用预设风格，每个风格是由一组 正向/负向提示词 组成，用于在视觉风格上限定后续的所有生成环节，建立全局统一的视觉标准。
  <img src="docs/images/ArtDirection_example.jpg" alt="LumenX Studio ArtDirection" width="100%" />

  Step 3: Assets 资产生成

  该阶段可以为Step 1所抽取的角色、场景、道具等资产进行文本描述编辑，再基于该文本描述生成对应的图片。对于角色而言，为了保持角色在不同场景的一致性，采用先生成人物的无背景全身图，再基于该全身图作为参考图生成对应的 三视图、头像特写 作为该角色的核心资产，后续若涉及到更换服装、形态，皆可以沿用该全身照或三视图进行二次图像编辑得来。
  另外，万相2.6系列支持参考生视频，所以此处也可以为每个橘色、场景、道具生成参考视频。

  <img src="docs/images/Assets_example.jpg" alt="LumenX Studio Assets" width="100%" />

  Step 4 StoryBoard 分镜图故事版

  该阶段可以基于脚本提取分镜脚本，形成结构化的故事版，支持用户的对分镜的二次编辑与增加、删除；对于每个分镜场景可以选择该场景所参与的角色、场景、道具作为参考图，进行分镜图的生成。
  该阶段融入AI提示词润色能力，可以直接利用Qwen-Plus对现有提示词进行润色，已嵌入图像编辑的提示词指南作为最佳实践。

  <img src="docs/images/StoryBoard_example.jpg" alt="LumenX Studio StoryBoard" width="100%" />

  Step 5 Motion 分镜视频生成

  该阶段可分为两种生成模型，一种是基于首帧驱动的i2v模式，另一种是基于角色动作驱动的r2v模式。在i2v模式下，可选取Step 4所生成的分镜图，逐一为其生成分镜视频，该生成过程也已融入AI提示词润色能力，可以直接利用Qwen-Plus对现有提示词进行润色，已嵌入图生视频的提示词指南作为最佳实践。在r2v模式下，可选取角色、场景、道具的参考视频进行参考生视频。
  同时该阶段支持 多Batch Size生成的抽卡机制，可在Step 6针对每个分镜进行最终分镜视频的选取。

  <img src="docs/images/Motion_example.jpg" alt="LumenX Studio Motion" width="100%" />

  Step 6 Assembly 分镜视频拼接

  该阶段可以审查每个分镜的分镜视频，选择你认为最好的，作为最终分镜，全部分镜选择结束后，点击“Merge&Proceed”按钮即可一键拼接成片。

  <img src="docs/images/Assembly_example.jpg" alt="LumenX Studio Assembly" width="100%" />
</div>

---

## 🏗️ 系统架构

LumenX Studio 采用前后端分离的现代化架构，确保了扩展性与性能。

<div align="center">
  <!-- 架构图 -->
  <img src="docs/images/architecture.svg" alt="System Architecture" width="80%" />
</div>

**技术栈：**
- **Frontend**: Next.js 14 + React 18 + TypeScript + Tailwind CSS
- **Backend**: FastAPI + Python 3.11+
- **AI Core**: Alibaba Cloud Qwen (Logic) + Wanx (Visuals)
- **Render**: Three.js (Canvas) + FFmpeg (Video Processing)

---

## 🚀 快速开始

### 1. 环境准备

- **Python**: 3.11+
- **Node.js**: 18+
- **FFmpeg**: 必须安装 (用于视频处理)

### 2. 克隆项目

```bash
git clone https://github.com/alibaba/lumenx.git
cd lumenx
```

### 3. 配置密钥

复制配置文件并填入 API Key（需开通阿里云百炼服务）：

```bash
cp .env.example .env
# 编辑 .env 文件，填入 DASHSCOPE_API_KEY
```

### 4. 启动后端

```bash
# 安装依赖
pip install -r requirements.txt

# 创建输出目录
mkdir -p output/uploads

# 启动服务 (http://localhost:8000)
./start_backend.sh
```

### 5. 启动前端

```bash
cd frontend

# 安装依赖 & 启动服务 (http://localhost:3000)
npm install && npm run dev
```

---

## 📖 文档中心

- **[用户手册](USER_MANUAL.md)**: 必读！详细的功能使用说明。
- **[API 文档](http://localhost:8000/docs)**: 后端接口定义的 Swagger UI。

---

## ⚙️ 进阶配置

<details>
<summary>点击展开详细配置说明</summary>

### OSS 对象存储（推荐）
为了安全和性能，建议配置阿里云 OSS 存储生成的媒体文件：

1. 创建 **私有 (Private)** Bucket
2. 在 `.env` 或应用设置中配置：
   ```env
   ALIBABA_CLOUD_ACCESS_KEY_ID=...
   ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
   # 在应用内配置 Bucket 名称和 Endpoint
   ```

### 配置文件路径
- **开发模式**: 项目根目录 `.env`
- **打包应用**: 用户主目录 `~/.lumen-x/config.json`

</details>

---

## 📁 目录结构

```
lumenx/
├── frontend/          # Next.js 前端工程
├── src/               # Python 后端核心
│   ├── apps/         # 业务逻辑
│   ├── models/       # AI 模型接口
│   └── utils/        # 工具库
├── output/            # (自动生成) 项目输出目录
```

---

## 🤝 参与贡献

我们非常欢迎社区贡献！请先阅读 [贡献指南](CONTRIBUTING.md) 了解代码规范和提交流程。

- **Bug 反馈**: 请提交 [GitHub Issues](https://github.com/alibaba/lumenx/issues)
- **功能建议**: 欢迎在 [Discussions](https://github.com/alibaba/lumenx/discussions) 中讨论

## 👤 作者与联系方式

本项目由 **星莲 (StarLotus)** 主导开发与维护。

如果您在使用过程中遇到问题，或有任何建议，欢迎通过以下方式联系：

- **反馈与交流**: [GitHub Issues](https://github.com/alibaba/lumenx/issues)
- **技术讨论**: [GitHub Discussions](https://github.com/alibaba/lumenx/discussions)
- **邮件联系**: [zhangjunhe.zjh@alibaba-inc.com](mailto:zhangjunhe.zjh@alibaba-inc.com)

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

<div align="center">
  Made with ❤️ by Alibaba Group
</div>

