# 六一那天测试项目

这个目录是【六一那天】的独立测试项目夹，用于验证“剧本分镜已确定后，生成高质量基础素材并回填分镜参考”的链路。

它只属于 fixture 回放与回归验证，不是产品级最终设计；产品级口径见 [分镜-素材-分镜图最终设计](../../../../docs/storyboard-consistency-final-design.md)。

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
- 第 01-18 镜的静帧文件完成不再等同于视觉一致：01 / 03 / 04 / 05 / 07 / 11 / 12 / 13 / 14 是拼图静帧基线，02 / 06 / 08 / 09 / 10 必须经过 `liuyi_char_xiaoqi_child_full_body.png` 的 child Xiaoqi identity-preserve workflow，并通过参考 patch 嵌入与 compose 回写视觉门禁；完整导出清单见 `generation_prompts/static_frame_exports.json`。
- 第 15 镜默认采用两段式稳定流程：先生成基础构图，再做单参考图局部一致性修整，不直接一次性多图 edit；Stage2 必须和 base crop 真的不一样，Stage3 必须按 bbox 回写一致。
- 第 15 镜可复跑 prompt 包已固化在 `generation_prompts/frame_15/`：`01_base_room_t2i_prompt.txt` 负责无参考图病房底图，`02a/02b/02c` 分别只处理成年小琪、男孩、父亲局部一致性，`99_fallback_multiref_low_semantic_risk_prompt.txt` 仅用于低语义强度的一次性多参考图 smoke。
- 第 16-18 镜已补齐正式 crop workflow，可直接沿 `generation_prompts/frame_16/`、`frame_17/`、`frame_18/` 复跑；这三镜同样要过像素级 visual gate，不再只看文件存在。
- 18 镜脚本实际使用到的全部角色、场景与道具参考图已经全部接回 manifest，并同步到 `output/uploads/fixtures/`；其中 `static_frame_exports.json` 虽然现在给每个静帧都显式标了 `visual_gate`，但拼图静帧的门禁只代表文件完成，不等于视觉齐，视觉一致仍要看对应 `visual_gate`。
- Seedance 只做 payload 预览，未确认成本前不要触发真实视频生成。
- 分镜中文字以剪辑后期叠加为准，避免图像模型生成不可控可读文字。
