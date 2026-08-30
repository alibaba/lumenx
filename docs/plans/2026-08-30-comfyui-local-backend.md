# LumenX ComfyUI 本地化改造（2026-08-30）

## 目标

把 LumenX Studio 的图像/视频生成从阿里云百炼切换到本地 ComfyUI，LLM 走任意
OpenAI 兼容接口（Ollama/vLLM 等），音频可选走 ComfyUI FishAudio 工作流，
同时保留云端 provider 作为可选项，不破坏既有流程与 UI。

## 参考实现

[brokenmoonbeam/lumenx-comfyui](https://github.com/brokenmoonbeam/lumenx-comfyui)
提供了 ComfyUI 图像/视频适配器和工作流映射的成熟范例。本改造将其思想移植到
当前 main 分支（含 model catalog、provider registry、playground 的新架构）上。

## 改动清单

### 新增

- `src/models/comfyui_client.py` — 双协议 ComfyUI 客户端（ZEALMAN 面板 +
  原生 ComfyUI API），含工作流列表/提交/轮询/上传/下载。
- `src/models/comfyui_image.py` — 图像适配器，接口对齐 `WanxImageModel.generate`。
- `src/models/comfyui_video.py` — 视频适配器，接口对齐 `WanxModel.generate`，
  支持 i2v / r2v / 首尾帧。
- `src/models/comfyui_audio.py` — 音频适配器（FishAudio 声音克隆），可选启用。
- `config/workflow_mapping.json` — 功能/模型 → ComfyUI 工作流映射（含节点映射）。
- `config/model_catalog/families/comfyui.yaml` — 本地模型族（T2I/I2I/I2V/R2V），
  前端模型选择器自动展示。
- `tests/test_comfyui_client.py`、`tests/test_comfyui_models.py` — 单测。

### 修改

- `src/utils/provider_registry.py` / `src/utils/model_catalog.py` —
  新增 `comfyui` 后端。
- `src/models/factory.py` — ComfyUI 路由。
- `src/apps/comic_gen/assets.py` / `storyboard.py` / `video.py` / `pipeline.py` —
  按模型名自动路由到 ComfyUI 适配器。
- `src/apps/comic_gen/api.py` — 新增 `/comfyui/*` 只读端点
  （health / config / workflows / workflow / task）。
- `src/apps/comic_gen/llm_adapter.py` — 支持 `LLM_BASE_URL` / `LLM_API_KEY` /
  `LLM_MODEL_NAME` 别名，方便切换本地 LLM。
- `src/apps/comic_gen/audio.py` — `COMFYUI_TTS_ENABLED=1` 时走 ComfyUI 音频。
- `.env.example` — ComfyUI 配置说明。

## 验证结果

- `python scripts/build_model_catalog.py` + `validate_model_catalog.py` — PASSED。
- `pytest -q` — 221 passed；4 个失败为 Windows 路径分隔符的既有问题
  （`test_local_only_flow`、`test_provider_media`，与本次改动无关）。
- 新增 25 个 ComfyUI 相关用例全部通过。

## 下一步（需要用户侧配置）

1. 按 `docs/1-api-reference/comfyui-workflows.md` 配置 `COMFYUI_BASE_URL` 与协议。
2. 在项目设置里把模型切换到 ComfyUI 条目（或改 catalog 默认值后重建）。
3. 若用原生 ComfyUI，把导出的工作流 JSON 放入 `config/comfyui_workflows/`。
