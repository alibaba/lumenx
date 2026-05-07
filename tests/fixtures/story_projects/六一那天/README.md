# 六一那天测试项目

这个目录是【六一那天】的独立测试项目夹，用于验证“剧本分镜已确定后，生成高质量基础素材并回填分镜参考”的链路。

## 输入素材

- `source/08_seedance2_storyboard_prompts.md`：分镜脚本与 Seedance 提示词。
- `references/storyboard_reference_collage.png`：用户提供的分镜参考图拼图。
- `project_manifest.json`：项目级素材、模型与测试约束清单。

## 图像模型

本测试项目的文生图和图编都走 OpenAI-compatible 图像链路：

- `t2i_model`: `openai-image`
- `i2i_model`: `openai-image-edit`
- 运行模型：`gpt-image2`

运行环境需要配置：

```env
IMAGE_PROVIDER=openai
IMAGE_EDIT_PROVIDER=openai
OPENAI_IMAGE_MODEL=gpt-image2
OPENAI_IMAGE_EDIT_MODEL=gpt-image2
```

不要把真实 API Key 写入本目录。真实密钥继续放在本机运行时配置或设置页中。

## 测试约束

- 先做生图与分镜静态图质量验证。
- 第 15 镜默认采用两段式稳定流程：先生成基础构图，再做单参考图局部一致性修整，不直接一次性多图 edit。
- 第 15 镜可复跑 prompt 包已固化在 `generation_prompts/frame_15/`：`01_base_room_t2i_prompt.txt` 负责无参考图病房底图，`02a/02b/02c` 分别只处理成年小琪、男孩、父亲局部一致性，`99_fallback_multiref_low_semantic_risk_prompt.txt` 仅用于低语义强度的一次性多参考图 smoke。
- Seedance 只做 payload 预览，未确认成本前不要触发真实视频生成。
- 分镜中文字以剪辑后期叠加为准，避免图像模型生成不可控可读文字。
