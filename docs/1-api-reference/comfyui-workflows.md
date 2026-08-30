# ComfyUI 本地模型接入参考（2026-08-30）

> 原始来源：https://github.com/brokenmoonbeam/lumenx-comfyui （LumenX ComfyUI 本地化 fork）
> 捕获日期：2026-08-30
> 适用范围：本仓库 `config/model_catalog/families/comfyui.yaml` 中的全部模型

## 概述

LumenX 通过 `src/models/comfyui_client.py` 对接本地 ComfyUI 服务，图像、视频、音频生成
全部由 ComfyUI 工作流驱动，不再依赖阿里云百炼的生成接口。LLM 走 OpenAI 兼容接口
（本地可用 Ollama / vLLM / LM Studio）。

## 支持的 ComfyUI 工作流类型

| 能力 | 参考工作流 | 用途 |
|---|---|---|
| 文生图 T2I | C16 短剧文生图专用 / C07 Zimage 加速 / A01 Qwen2512 | 角色、场景、道具、分镜 |
| 图生图 I2I | B13 千问角色一键多角度 / D20 RAW 画质重建 | 角色一致性、三视图、头像 |
| 图生视频 I2V | G03/G10 Wan2.2 SmoothMix / H17/H28 LTX2.3 | 首帧驱动的镜头生成 |
| 首尾帧视频 | G02 首尾帧 Wan2.2 | 首尾帧约束的视频生成 |
| 动作迁移 R2V | P02/P07 Wan2.2 Animate | 参考视频驱动的角色迁移 |
| 声音克隆 TTS | N2 FishAudio S2pro | 本地语音合成（可选，`COMFYUI_TTS_ENABLED=1`） |

## 服务协议

客户端支持两种协议，通过 `COMFYUI_PROTOCOL` 切换：

- `standard`（默认）：原生 ComfyUI API（`POST /prompt`、`GET /history`、
  `POST /upload/image`、`GET /view`）；工作流模板放在 `config/comfyui_workflows/<workflow_id>.json`，
  `input_values` 使用 `"节点ID:字段"` 形式注入。
- `zealman`：ZEALMAN ComfyUI 控制面板 REST API
  （`/api/workflow/generate`、`/api/workflow/result`、`/api/comfy/upload/file`、
  `/api/comfy/view`、`/api/workflow/list`）。

## 配置

```dotenv
COMFYUI_BASE_URL=http://localhost:8188
COMFYUI_PROTOCOL=standard
COMFYUI_API_KEY=            # 可选
COMFYUI_TTS_ENABLED=0       # 1 时音频走 ComfyUI
```

工作流映射位于 `config/workflow_mapping.json`：

- `asset_generation` / `storyboard` / `video_generation` / `audio_generation`：
  功能 → 工作流 ID。
- `model_overrides`：具体模型 ID（如 `comfyui-wan2.2-i2v`）→ 工作流 ID。
- `node_mapping`：ZEALMAN 面板的输入节点字段（正向/负向提示词等）。

## 模型 ID

- 图像：`comfyui-wan2.2-t2i`（文生图）、`comfyui-wan2.2-i2i`（图生图）
- 视频：`comfyui-wan2.2-i2v`、`comfyui-wan2.2-r2v`、`comfyui-ltx2.3-i2v`
- 规范 ID 形如 `comfyui/comfyui-wan2.2-video#i2v`

模型名以 `comfyui/` 或 `comfyui-` 开头时，后端自动路由到本地 ComfyUI 适配器，
无需改动生成调用代码。

## 切换默认模型为完全本地模式

编辑 `config/model_catalog/catalog.meta.yaml` 的 `defaults.model_settings`，将
`t2i_model` / `i2i_model` / `image_model` / `i2v_model` / `r2v_model` 改为上面的
ComfyUI 模型 ID，然后运行：

```bash
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
```
