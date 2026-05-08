# Frame 01 Formal Static Workflow

这是 `liuyi_frame_01` 的正式静帧基线说明。当前帧是校园建立镜头，不需要局部角色 identity edit，正式静帧直接来自用户分镜拼图的无标签画面裁切。

## Formal Static

- Source collage: `tests/fixtures/story_projects/六一那天/references/storyboard_reference_collage.png`
- Collage bbox: `{ "x": 0, "y": 0, "width": 448, "height": 252 }`
- Final output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_01_stage3_full_formal_v1.png`
- Output size: `2048x1152`

## Reference Assets

- Scene: `references/liuyi_scene_school_playground.png`
- Characters: none
- Props: none

## Notes

- 这是 2008 六一校园建立镜头，主要锁定场景空间、暖金色日光和普通小学汇演质感。
- 这张静帧已纳入 `generation_prompts/static_frame_exports.json` 的文件完成清单；视觉门禁仍以对应 pytest 用例为准。
- 后续若要做模型重绘，再从 `tests/fixtures/story_projects/_templates/frame_crop_workflow/` 复制正式 crop workflow；当前基线不调用图像 API。
