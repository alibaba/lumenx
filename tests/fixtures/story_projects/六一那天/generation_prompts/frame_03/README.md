# Frame 03 Formal Static Workflow

这是 `liuyi_frame_03` 的正式静帧基线说明。当前帧用于建立白色毛绒小熊和纸袋的道具链，正式静帧直接来自用户分镜拼图的无标签画面裁切。

## Formal Static

- Source collage: `tests/fixtures/story_projects/六一那天/references/storyboard_reference_collage.png`
- Collage bbox: `{ "x": 896, "y": 0, "width": 448, "height": 252 }`
- Final output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_03_stage3_full_formal_v1.png`
- Output size: `2048x1152`

## Reference Assets

- Scene: `references/liuyi_scene_hospital_room.png`
- Character: `references/liuyi_char_father_full_body.png`
- Props: `references/liuyi_prop_white_bear.png`, `references/liuyi_prop_paper_bag.png`

## Notes

- 本帧必须保持小熊为白色、蓝色丝带、浅色纸袋和普通医院床头环境。
- 父亲在背景虚化中出现，本帧优先锁定道具关系；后续若要模型重绘，再从 `tests/fixtures/story_projects/_templates/frame_crop_workflow/` 复制正式 crop workflow。
- 这张静帧已纳入 `generation_prompts/static_frame_exports.json` 的文件完成清单；视觉门禁仍以对应 pytest 用例为准。
