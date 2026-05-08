# 分镜-素材-分镜图最终设计

日期：2026-05-08

这是一份产品级口径文件，定义“剧本 -> 素材 -> 分镜图”的最终设计。
它不是 `tests/fixtures/story_projects/六一那天/` 这类测试样板说明，也不是局部回放脚本说明。

## 设计目标

- 用剧本驱动分镜结构，用主参考素材驱动视觉一致性。
- 分镜图必须由图生图链路生成，默认不采用手工贴图式合成。
- 一致性依赖“主参考资产 + 分镜引用策略 + 连续性提示 + 质量门禁”，不是靠文件存在。
- fixture 目录只用于回归验证，不作为产品设计本体。

## 范围与非目标

- 本文定义的是产品级默认链路和约束，不是 fixture 回放脚本说明。
- 本文不把局部贴图、手工拼接、裁片回写当作默认产品方案。
- 本文不替代逐帧提示词创作，只定义引用、连续性和降级边界。
- 本文不要求所有帧都走同一种模型路径，但要求最终输出都回到“模型生成的单张分镜图”。

## 唯一链路

```mermaid
flowchart LR
  A[剧本 / 原文] --> B[story_analysis]
  B --> C[frames]
  C --> D[角色 / 场景 / 道具主参考]
  D --> E[buildStoryboardCompositionData]
  E --> F[generate_storyboard_render]
  F --> G[分镜图结果]
  G --> H[video / export]
```

## 参考层级

1. 剧本和结构化分镜是语义源头。
2. 角色、场景、道具的主参考图是视觉源头。
3. `art_direction.style_config` 是全局风格源头。
4. `frame.composition_data` 是单帧引用策略源头。
5. `frame.rendered_image_asset` 是该帧的最终图像结果容器。

## 职责分层

| 层级 | 主要职责 | 关键产物 | 明确不负责 |
| --- | --- | --- | --- |
| 前端 | 组装引用预览、写入 `composition_data`、提示 prompt 风险 | `reference_preview`、`reference_image_urls`、`continuity_lock` | 不做像素级合成 |
| 后端 | 汇总参考、补连续性提示、选择模型、执行渲染 | `final_prompt`、`render_strategy`、最终分镜图 | 不把 fixture compose 当产品输出 |
| fixture | 固化回放、复现样板、做视觉门禁 | `project_manifest.json`、crop manifest、回放结果 | 不代表真实产品默认链路 |

## 关键字段约定

- `frame.composition_data` 是前后端共享的引用契约容器。
- `reference_binding_version` 主要用于 fixture / 旧协议引用绑定，便于标记协议版本和避免历史字段漂移。
- `reference_image_url` / `reference_image_urls` 是实际喂给图像模型的引用入口。
- `reference_preview` 只用于前端解释和排查，不能代替真实引用传参。
- `continuity_lock` 控制同场景连续性是否接入前后镜头参考。
- `continuity_source_frame_id` 记录连续性来源，便于追查跨帧引用。
- `render_strategy` 只在安全降级时出现，说明为什么改为分阶段生成。

## 一致性策略

### 角色一致性

- 以角色 `full_body_asset` 为主参考。
- `three_view_asset`、`headshot_asset` 作为补充参考，不替代主参考。
- 角色锁定时，不能把贴图或手工裁片当作最终一致性手段。
- 引用优先级默认是 `three_view_asset` -> `full_body_asset` -> `headshot_asset` -> 其他可用图。

### 场景一致性

- 以场景 `image_asset` 为主参考。
- 保持空间布局、光线方向、入口位置、关键道具位置连续。
- 同场景相邻帧应默认带连续性提示。
- 场景参考优先使用已选 variant，退化到 `image_url` 仅作为兜底。

### 道具一致性

- 以道具 `image_asset` 为主参考。
- 保持轮廓、材质、磨损和摆放逻辑一致。
- 道具参考优先使用已选 variant，退化到 `image_url` 仅作为兜底。

### 分镜一致性

- 前端先组装 `reference_image_urls`、`continuity_lock`、`reference_preview`。
- 后端按当前帧的场景、角色、道具和风格参考生成最终 prompt。
- `inspectStoryboardPrompt()` 负责在生成前提示连续性或引用缺失风险。
- `inspectStoryboardPrompt()` 给的是风险提示，不是视觉结果；真正的一致性仍然由主参考图和模型图生图完成。

## 明确禁止

- 不把参考图直接当成画面里的“贴片元素”。
- 不把局部裁片合成当成产品默认渲染路径。
- 不把 fixture 的本地 compose 当成真实产品渲染。
- 不把“文件存在”当成视觉一致性的完成证据。

## 例外路径

- 对高风险题材或多参考风险帧，可以走受控的 staged render 思路。
- 该例外路径仍然属于模型生成策略，不是手工贴图方案。
- fixture 里的像素级 compose 只用于回归验证和门禁，不进入正常产品输出链路。
- 当前实现里，命中医疗 + 未成年人等高风险上下文时，会切到 `staged_safe_storyboard`：先出基础构图，再做单参考局部校准，最后做一致性收尾。

## 验收标准

| 层级 | 验收要点 |
| --- | --- |
| 产品输出 | 分镜图在语义上承接当前分镜，在视觉上承接对应主参考资产；结果应是统一构图的单张图，而不是可见拼接痕迹。 |
| 前端 | 参考预览、连续性开关和 prompt 风险提示与真实 `composition_data` 一致。 |
| 后端 | 参考图已进入 `ref_image_paths`，同场景时补了连续性提示；必要时会写入 `render_strategy`。 |
| fixture | manifest、crop、compose、pixel gate 一致，且只用于回归验证和门禁。 |

补充约束：

- 关键角色、场景、道具不应漂移成另一套身份。
- 对需要连续性的镜头，前后帧应保持同一空间和同一时序逻辑。
- 对文字类内容，优先后期叠加，不依赖图像模型直接生成可读文字。

## 与仓库文件的对应关系

- [src/apps/comic_gen/pipeline.py](../src/apps/comic_gen/pipeline.py): 后端分镜分析、引用收集、渲染与安全策略入口。
- [frontend/src/lib/storyboard-references.ts](../frontend/src/lib/storyboard-references.ts): 前端分镜引用预览与 composition data 组装。
- [frontend/src/lib/prompt-quality.ts](../frontend/src/lib/prompt-quality.ts): 分镜 prompt 质量检查。
- [tests/fixtures/story_projects/六一那天/README.md](../tests/fixtures/story_projects/六一那天/README.md): 测试样板说明，不是最终设计。
- [tests/fixtures/story_projects/六一那天/generation_prompts/README.md](../tests/fixtures/story_projects/六一那天/generation_prompts/README.md): fixture 回放与视觉门禁说明。
- [tests/fixtures/story_projects/六一那天_v2/project_manifest.draft.json](../tests/fixtures/story_projects/六一那天_v2/project_manifest.draft.json): 新项目重制版草案 manifest。
- [tests/fixtures/story_projects/六一那天_v2/references/ASSET_INVENTORY.md](../tests/fixtures/story_projects/六一那天_v2/references/ASSET_INVENTORY.md): 新项目主板 + 派生图资产清单。

## 结论

产品级最终设计应以“主参考图驱动的图生图分镜链路”为准。
局部贴图或本地 compose 只能作为测试/验收工具，不能被误当成默认设计。

## 正式版 18 张执行清单

交付口径只有一个：`frame.rendered_image_asset` 指向的最终分镜图。
在【六一那天】验收项目里，它统一落地到 `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_NN_stage3_full_formal_v1.png`。
`output/codex_image_audit/liuyi-that-day/generated/` 本身是工作输出目录，会保留 stage1 / stage2 / contact sheet 等中间产物；正式交付只看 18 张 `stage3_full_formal_v1.png`。
`output/uploads/fixtures/` 只承接 fixture 导入副本，不是最终交付目录。

| 帧组 | 帧号 | 主要输入 | 必过门禁 | 默认排查 |
| --- | --- | --- | --- | --- |
| 静帧基线 | 01 / 03 / 04 / 05 / 07 / 11 / 12 / 13 / 14 | `storyboard_reference_collage.png` + `08_seedance2_storyboard_prompts.md` | `test_liuyi_static_frame_exports_are_complete_and_openable`；最终文件可打开 | 前端组引用 -> fixture 回放 |
| identity-preserve | 02 / 06 / 08 / 09 / 10 | `liuyi_char_xiaoqi_child_full_body.png` + patch / compose manifest | `test_liuyi_child_identity_visual_gate_embeds_locked_reference` | 前端组引用 -> 后端渲染 -> fixture 回放 |
| formal crop workflow | 15 / 16 / 17 / 18 | base 图、base crop、edited crop、crop manifest | `test_liuyi_formal_crop_workflows_change_pixels_and_compose_outputs`；`compose_fixture_frame_crops.ps1 -DetectOnly` | 后端渲染 -> fixture 回放 |

执行入口统一看这三处：

- 静帧基线：`scripts/run_fixture_frame_script.ps1`
- identity-preserve：`scripts/run_fixture_frame_script.ps1` + `scripts/compose_liuyi_child_identity_crop.py`
- formal crop workflow：`scripts/run_fixture_frame_script.ps1` + `scripts/compose_fixture_frame_crops.ps1`

## 产品级链路 vs fixture 回放链路

| 维度 | 产品级链路 | fixture 回放链路 |
| --- | --- | --- |
| 目标 | 产出可交付的真实分镜图 | 复现样板项目的参考、裁片与门禁 |
| 输入 | 剧本、结构化分镜、主参考素材、风格配置 | `project_manifest.json`、固定参考图、base crop、edited crop、compose manifest |
| 参考图使用方式 | 由前端组装 `composition_data`，后端交给图生图模型融合 | 按 manifest 或本地脚本做裁片回放与像素合成 |
| 一致性来源 | 主参考资产 + 连续性提示 + prompt 质量检查 + 模型图生图 | 固化 bbox、参考 patch、像素差门禁、Stage3 compose 回写 |
| 生成路径 | `buildStoryboardCompositionData()` -> `generate_storyboard_render()` | `compose_liuyi_child_identity_crop.py` / `compose_fixture_frame_crops.ps1` |
| 输出形态 | 单张统一构图的分镜图 | 验证用基线图、裁片、回放图、导出清单 |
| 失败含义 | 说明产品链路的引用、prompt 或模型输出有问题 | 说明样板门禁、bbox、裁片或本地回放有问题 |
| 是否可替代产品设计 | 是 | 否 |

### 快速排查

- 如果“分镜图像不像统一生成的结果，而像贴上去的”，先看 fixture 回放链路是否被误用。
- 如果“参考图没进模型、连续性没生效”，优先查 [frontend/src/lib/storyboard-references.ts](../frontend/src/lib/storyboard-references.ts) 和 [src/apps/comic_gen/pipeline.py](../src/apps/comic_gen/pipeline.py)。
- 如果“样板项目门禁没过”，优先查 [tests/fixtures/story_projects/六一那天/generation_prompts/README.md](../tests/fixtures/story_projects/六一那天/generation_prompts/README.md) 和 [scripts/compose_liuyi_child_identity_crop.py](../scripts/compose_liuyi_child_identity_crop.py)。

## 默认排查顺序

1. 前端组引用：先看 `reference_preview`、`reference_image_urls`、`continuity_lock` 有没有按帧组装对。
2. 后端渲染：再看 `ref_image_paths`、`final_prompt`、`render_strategy` 有没有把参考和连续性真正吃进去。
3. fixture 回放：最后看 manifest、crop、compose、pixel gate 有没有对上。

## 常见故障 -> 看哪一层

| 现象 | 优先看哪一层 | 先查什么 |
| --- | --- | --- |
| 前端预览里缺少参考图、连续性开关没生效、生成前提示词很怪 | 前端 | [frontend/src/lib/storyboard-references.ts](../frontend/src/lib/storyboard-references.ts)、[frontend/src/lib/prompt-quality.ts](../frontend/src/lib/prompt-quality.ts)、[frontend/src/components/modules/StoryboardFrameEditor.tsx](../frontend/src/components/modules/StoryboardFrameEditor.tsx) |
| 后端生成结果人物/场景/道具漂移，或图生图没把参考吃进去 | 后端 | [src/apps/comic_gen/pipeline.py](../src/apps/comic_gen/pipeline.py)、[src/models/image.py](../src/models/image.py)、[src/apps/comic_gen/prompt_recipes.py](../src/apps/comic_gen/prompt_recipes.py) |
| fixture 图看起来像贴图、裁片回放发虚、Stage3 不一致 | fixture | [tests/fixtures/story_projects/六一那天/generation_prompts/README.md](../tests/fixtures/story_projects/六一那天/generation_prompts/README.md)、[scripts/compose_liuyi_child_identity_crop.py](../scripts/compose_liuyi_child_identity_crop.py)、[tests/test_fixture_story_project_import.py](../tests/test_fixture_story_project_import.py) |
| 前后端都正常，但样板门禁失败或 bbox 对不上 | fixture + 后端交界 | [tests/fixtures/story_projects/六一那天/project_manifest.json](../tests/fixtures/story_projects/六一那天/project_manifest.json)、[tests/fixtures/story_projects/六一那天/generation_prompts/README.md](../tests/fixtures/story_projects/六一那天/generation_prompts/README.md) |
