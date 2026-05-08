# Frame 16 Crop Workflow

这是 `liuyi_frame_16` 的正式 crop workflow 目录，来源于 `_templates/frame_crop_workflow/`，已补齐 base 图、adult Xiaoqi crop 和 bbox。

当前资产：

- Base: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_16_stage1_base.png`
- Base crop: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_16_stage1_base_crop_adult_xiaoqi.png`
- Edited crop: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_16_stage2_adult_xiaoqi_formal_v1.png`
- Final output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_16_stage3_full_formal_v1.png`
- bbox: `{ "x": 1280, "y": 256, "width": 768, "height": 896 }`

## Visual Gate

- Stage2 crop 必须相对 base crop 发生真实像素变化。
- Stage3 的 bbox 区域必须与 edited crop 像素一致。

统一入口：

- 正式编辑只走 `scripts/run_fixture_frame_script.ps1`
- 合回 / bbox 检测只走 `scripts/compose_fixture_frame_crops.ps1`

推荐顺序：

1. base 或 crop 变更后，先跑 `scripts\compose_fixture_frame_crops.ps1 -ProjectSlug liuyi-that-day -FrameId liuyi_frame_16 -DetectOnly` 校验 bbox。
2. 再从 `scripts\run_fixture_frame_script.ps1` 进入正式编辑。
3. 正式编辑完成后用 `scripts\compose_fixture_frame_crops.ps1 -ProjectSlug liuyi-that-day -FrameId liuyi_frame_16` 合回。
