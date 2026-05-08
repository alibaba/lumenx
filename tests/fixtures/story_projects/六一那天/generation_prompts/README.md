# 六一那天分镜模板索引

`frame_01/` 到 `frame_18/` 都声明落在 `output/codex_image_audit/liuyi-that-day/generated/liuyi_frame_NN_stage3_full_formal_v1.png`，但验收口径是“文件完成 + 视觉门禁”，不再等同于“文件存在”。

产品级最终设计口径见 [分镜-素材-分镜图最终设计](../../../../../docs/storyboard-consistency-final-design.md)。
正式版 18 张的输出目录和验收门禁以该文档中的“正式版 18 张执行清单”为准；本目录只保存 fixture 回放包。
重制版骨架另见 [六一那天_v2 draft manifest](../../六一那天_v2/project_manifest.draft.json) 和 [v2 资产清单](../../六一那天_v2/references/ASSET_INVENTORY.md)，不要把本目录的回放包当成 v2 正式设计。

完整导出清单见 `static_frame_exports.json`：

- `frame_01/03/04/05/07/11/12/13/14`：拼图静帧基线，`static_frame_exports.json` 里也显式带 `visual_gate=test_liuyi_static_frame_exports_are_complete_and_openable`，但这只代表文件完成，不代表视觉一致。
- `frame_02/06/08/09/10`：必须经过 `liuyi_char_xiaoqi_child_full_body.png` 的 child Xiaoqi identity-preserve workflow，并通过参考 patch 嵌入与 compose 回写视觉门禁。
- `frame_15/16/17/18`：正式 crop workflow，必须通过 base-vs-edited 像素差与 Stage3 compose 回写门禁。

`static_frame_exports.json` 的 `status` 只声明“18 张输出文件已声明且可打开”的文件完成状态，不能单独作为视觉一致性完成证明。视觉一致性必须以 `tests/test_fixture_story_project_import.py::test_liuyi_child_identity_visual_gate_embeds_locked_reference` 和 `tests/test_fixture_story_project_import.py::test_liuyi_formal_crop_workflows_change_pixels_and_compose_outputs` 等门禁通过为准。

后续新增 identity-sensitive 帧时，不能只把参考图写进 manifest；必须补 prompt、crop manifest、edited crop、compose 回写和像素级视觉门禁：

- `tests/fixtures/story_projects/_templates/frame_crop_workflow/`

统一入口：

- 正式编辑：`scripts/run_fixture_frame_script.ps1`
- 合回 / bbox 检测：`scripts/compose_fixture_frame_crops.ps1`
- 小七儿童 identity 本地合成：`scripts/compose_liuyi_child_identity_crop.py`
