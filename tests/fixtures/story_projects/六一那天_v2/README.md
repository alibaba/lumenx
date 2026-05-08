# 六一那天_v2

这是【六一那天】的重制版规划目录。

当前这里只放重制版骨架、draft manifest、source 重写稿和资产清单，不是正式 fixture 项目。
完整规划见 [docs/storyboard-rebuild-plan.md](../../../../docs/storyboard-rebuild-plan.md)。

## 当前边界

- `project_manifest.draft.json` 只定义字段草案和资产包结构，不会被 `list_fixture_story_projects()` 当成正式项目。
- `source/05_storyboard_script.md` 是重写后的 18 镜分镜脚本，作为本项目 canonical 口径；旧项目 prompt 只可当 cinematic polish 参考，不再当剧情标准。
- `references/ASSET_INVENTORY.md` 是主板 + 派生图资产清单，后续真实 PNG 要按这里落位。
- 当前正式导入只要求清单里的必需主板 / 必需派生图；可选 headshot、lighting variant、time variant 仍保持 draft 口径。
- 等真实新素材图补齐并通过门禁后，再把 draft 升级为 `project_manifest.json`，进入正式 fixture discovery。
