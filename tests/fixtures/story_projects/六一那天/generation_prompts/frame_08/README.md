# Frame 08 Child Identity Workflow

这是 `liuyi_frame_08` 的 child Xiaoqi identity-preserve workflow。

## Child Identity

- Source collage: `tests/fixtures/story_projects/六一那天/references/storyboard_reference_collage.png`
- Source collage bbox: `{ "x": 448, "y": 572, "width": 448, "height": 252 }`
- Identity reference: `output/uploads/fixtures/liuyi_char_xiaoqi_child_full_body.png`
- Reference source bbox: `{ "x": 300, "y": 80, "width": 420, "height": 420 }`
- Identity crop bbox: `{ "x": 208, "y": 0, "width": 832, "height": 1152 }`
- Identity patch bbox: `{ "x": 0, "y": 110, "width": 360, "height": 360 }`
- Base crop: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_08_stage1_collage_base_crop_child_xiaoqi.png`
- Edited crop: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_08_stage2_child_xiaoqi_formal_v1.png`
- Final output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_08_stage3_full_formal_v1.png`
- Output size: `832x1152` crop, `2048x1152` compose output
- Stage2 composer: `scripts/compose_liuyi_child_identity_crop.py`

## Notes

- 这张帧不再接受纯 collage export；它必须经过 child Xiaoqi identity edit。
- 视觉门禁会检查 crop 是否真的嵌入 `liuyi_char_xiaoqi_child_full_body.png` 的参考 patch，并检查整帧 compose 回写，不把文件存在等同于视觉一致。
