# Frame 13 Formal Static Workflow

这是 `liuyi_frame_13` 的正式静帧基线说明。当前帧不走局部 identity edit，正式静帧直接来自分镜拼图裁切。

## Formal Static

- Source collage: `tests/fixtures/story_projects/六一那天/references/storyboard_reference_collage.png`
- Collage bbox: `{ "x": 0, "y": 1144, "width": 448, "height": 252 }`
- Final output: `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_13_stage3_full_formal_v1.png`
- Output size: `2048x1152`

## Reference Assets

- Scene: `references/liuyi_scene_medical_school.png`
- Characters: `references/liuyi_char_xiaoqi_young_full_body.png`
- Props: `references/liuyi_prop_medical_textbooks.png`

## Unified Entry

- 正式编辑：`scripts/run_fixture_frame_script.ps1`
- 合回 / bbox 检测：`scripts/compose_fixture_frame_crops.ps1`

## Notes

- 这张静帧已纳入 `generation_prompts/static_frame_exports.json` 的文件完成清单；视觉门禁仍以对应 pytest 用例为准。
- 后续若要模型重绘，再从 `tests/fixtures/story_projects/_templates/frame_crop_workflow/` 复制正式 crop workflow；当前基线不调用图像 API。
