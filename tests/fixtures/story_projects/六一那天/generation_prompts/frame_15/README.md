# Frame 15 Stable Two-Stage Image Workflow

本目录是 `liuyi_frame_15` 的可复现 prompt/SOP 包。默认策略是先生成无参考图的基础构图，再对成人角色做单参考图局部 edit，最后用本地脚本把 edited crop 合回完整图。这样可以避开“儿童 + 病房 + 多参考图”组合带来的 moderation/429 风险。

## Stable SOP

1. 生成基础病房构图。
   - Prompt: `01_base_room_t2i_prompt.txt`
   - 输入：纯文本，不挂角色、道具、场景参考图。
   - 已验收基础图：`output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_15_stage1_base_v3.png`
   - 验收重点：病房几何、三人站位、男孩和气球轮廓成立。

2. 局部编辑成年小琪。
   - Prompt: `02a_adult_xiaoqi_crop_edit_prompt.txt`
   - Target crop: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_15_stage1_base_v3_crop_xiaoqi.png`
   - Identity reference: `output/uploads/fixtures/liuyi_char_xiaoqi_adult_full_body.png`
   - Crop bbox: `x=1360, y=32, width=640, height=1088`
   - Formal output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_15_stage2a_xiaoqi_crop_formal_v1.png`

3. 局部编辑父亲。
   - Prompt: `02c_father_crop_edit_prompt.txt`
   - Target crop: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_15_stage1_base_v3_crop_father.png`
   - Identity reference: `output/uploads/fixtures/liuyi_char_boy_father_full_body.png`
   - Crop bbox: `x=0, y=256, width=1024, height=768`
   - Formal output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_15_stage2c_father_crop_formal_v1.png`

4. 本地合回完整图。
   - Manifest: `crop_composition_manifest.json`
   - Script: `scripts/compose_fixture_frame_crops.ps1`
   - Final output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_15_stage3_full_formal_v1.png`
   - 这一步不调用图像 API，只做本地像素合成。

## Visual Gate

- `adult_xiaoqi` 与 `boy_father` 的 Stage2 crop 必须相对各自 base crop 发生真实像素变化。
- Stage3 的对应 bbox 必须与 edited crop 像素一致，而不是只保证文件存在。

男孩相关的 `02b_boy_local_edit_prompt.txt` 仍只作为受控诊断 prompt，不进入默认正式路径。

## Project Wrapper Inputs

正式产品化调用应走项目内 `WanxImageModel / openai-image-edit` 路径，并让 `src/models/image.py` 自动处理参考图预处理、尺寸日志和多参考限制。每个 crop edit 只传入两张图：

- Image 1: 当前 crop target。
- Image 2: 对应成人角色参考图。

不要把完整病房图、儿童参考图和多张角色参考一次性传给同一个 edit 请求。

## Local Commands

正式入口优先走 ASCII wrapper，避免 PowerShell 在中文目录里读路径字面量：

```powershell
powershell -File scripts\run_fixture_frame_script.ps1 `
  -ProjectSlug liuyi-that-day `
  -FrameId liuyi_frame_15 `
  -ScriptName run_formal_crop_edits.ps1
```

现成的项目别名也可以直接用：

```powershell
powershell -File scripts\run_liuyi_frame15_formal_crop_edits.ps1
```

这套模板只作为 `liuyi_frame_15` 的现成示例。后续新 frame 直接复制 `tests/fixtures/story_projects/_templates/frame_crop_workflow/`，再替换 bbox、输出文件名、prompt 路径和参考图即可。

只做本地合回或 bbox 检测时，也走 ASCII compose-only wrapper：

```powershell
powershell -File scripts\compose_fixture_frame_crops.ps1 `
  -ProjectSlug liuyi-that-day `
  -FrameId liuyi_frame_15
```

```powershell
powershell -File scripts\compose_fixture_frame_crops.ps1 `
  -ProjectSlug liuyi-that-day `
  -FrameId liuyi_frame_15 `
  -DetectOnly
```

## Optional Fallback

`99_fallback_multiref_low_semantic_risk_prompt.txt` 是一次性多参考 smoke prompt，已刻意降低未成年人和病房语义强度。它不是正式生产路径。

如果 fallback 被拦截或遇到 429，不要加强语义重试；回到 staged workflow，并优先复用已产出的 crop 和本地合回脚本。
