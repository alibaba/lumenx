# 六一那天_v2 新项目规划

日期：2026-05-08

这是一套从零重建的项目规划，不改旧项目，不复用旧 prompt 文本。

## 1. 目录与命名

- 新目录：`tests/fixtures/story_projects/六一那天_v2/`
- 新 slug：`liuyi-that-day-v2`
- 项目名：`六一那天·重制版`
- 项目类型：`seedance_storyboard_rebuild`
- 当前状态：`draft`，直到资产包、source 和 18 镜脚本全部补齐后再转正式 fixture

## 2. `project_manifest.json` 字段草案

这个项目建议把“主板资产”和“运行时资产”分开管理。

| 字段 | 作用 | 说明 |
| --- | --- | --- |
| `schema_version` | 清晰标记 manifest 协议版本 | 新项目直接用 `2` |
| `slug` | fixture 唯一标识 | `liuyi-that-day-v2` |
| `project_name` | 展示名 | `六一那天·重制版` |
| `project_type` | 项目类型 | `seedance_storyboard_rebuild` |
| `project_stage` | 当前阶段 | `draft` / `asset_building` / `prompt_writing` / `ready` |
| `parser` | source 解析器 | 继续沿用 `seedance_storyboard_markdown` |
| `source_files` | 源文档清单 | story bible、视觉 bible、角色/场景/道具 bible、18 镜脚本 |
| `reference_images` | 项目级主板图 | storyboard collage、style board、各角色/场景/道具 4K 主板 |
| `asset_packages` | 主板到派生图的作者级清单 | 记录 board -> full body / three view / expression / detail 的对应关系 |
| `reference_assets` | 运行时兼容清单 | 从 `asset_packages` 扁平化而来，给现有导入器和渲染链路用 |
| `model_settings` | 模型选择 | 保留现有 `openai-image` / `openai-image-edit` / `gpt-image2` 口径 |
| `asset_policy` | 资产标准 | 分辨率、视图数量、表情板数量、命名规则 |
| `render_policy` | 输出标准 | 统一输出目录、文件命名、可保留中间产物的范围 |
| `notes` | 备注 | 说明重做范围、禁用旧文本、验收门禁 |

### 2.1 `asset_packages` 约定

- 这是作者级清单，不直接替代运行时资产。
- 每个包都应有一个 4K 主板。
- 字符资产包必须包含 `full_body`、`three_view`、`expression_sheet`，成年角色可补 `headshot`。
- 场景资产包必须包含 `board` 和 `key_view`，必要时补光线/时间变体。
- 道具资产包必须包含 `board` 和 `detail`，必要时补使用场景图。

### 2.2 `reference_assets` 约定

- 这是运行时兼容层，维持当前导入和渲染可用。
- 允许先由 `asset_packages` 扁平化生成，再写回 manifest。
- 旧的 `full_body` / `three_views` / `head_shot` / `image` 仍可保留。
- 新项目不再把“单张全身图”当作全部角色资产。

## 3. 资产清单

### 3.1 角色资产

每个角色都按同一套标准补全：

- `board_4k`：一张 4K 角色主板，包含基本图、三视图、表情图
- `full_body`：全身主图，供分镜和身份锁定使用
- `three_view`：三视图，供造型一致性使用
- `expression_sheet`：表情板，供情绪分镜使用
- `headshot`：成年角色可追加，供特写镜头使用

角色清单：

- `liuyi_char_xiaoqi_child_v2`：小琪（儿童）
- `liuyi_char_mother_v2`：母亲
- `liuyi_char_father_v2`：父亲
- `liuyi_char_xiaoqi_young_v2`：小琪（少年）
- `liuyi_char_xiaoqi_adult_v2`：小琪（成年）
- `liuyi_char_boy_v2`：2026 小男孩
- `liuyi_char_boy_father_v2`：2026 小男孩父亲

### 3.2 场景资产

每个场景都按同一套标准补全：

- `board_4k`：一张 4K 场景主板
- `key_view`：最常用机位
- `lighting_variant`：可选，用于同场景连续性

场景清单：

- `liuyi_scene_school_playground_v2`
- `liuyi_scene_school_gate_v2`
- `liuyi_scene_hospital_room_v2`
- `liuyi_scene_2026_ward_v2`
- `liuyi_scene_hospital_corridor_v2`
- `liuyi_scene_funeral_hall_v2`
- `liuyi_scene_home_desk_v2`
- `liuyi_scene_exam_admission_v2`
- `liuyi_scene_medical_school_v2`
- `liuyi_scene_doctor_office_v2`

### 3.3 道具资产

每个道具都按同一套标准补全：

- `board_4k`：一张 4K 道具主板
- `detail`：材质和结构细节图
- `usage_view`：在场景里的使用图

道具清单：

- `liuyi_prop_white_bear_v2`
- `liuyi_prop_paper_bag_v2`
- `liuyi_prop_child_drawing_v2`
- `liuyi_prop_childrens_day_balloons_v2`
- `liuyi_prop_medical_textbooks_v2`
- `liuyi_prop_father_memorial_portrait_v2`
- `liuyi_prop_family_photo_v2`
- `liuyi_prop_notebook_pencil_v2`
- `liuyi_prop_admission_notice_v2`

### 3.4 全局风格资产

- `liuyi_style_board_v2`
- `liuyi_storyboard_reference_collage_v2`

## 4. `source/` 重写方案

source 不复用旧 prompt 文本，只保留故事骨架和 parser 友好的 markdown 结构。

建议文件：

- `source/01_story_bible.md`
- `source/02_visual_bible.md`
- `source/03_character_bible.md`
- `source/04_scene_prop_bible.md`
- `source/05_storyboard_script.md`

### 写作规则

- 允许沿用同一故事事实，但不能沿用旧句子。
- 18 镜分镜必须重新组织语言、镜头顺序和画面描述。
- 每个 shot 都要明确 scene、characters、props、camera、emotion、composition。
- 不再使用旧项目中的提示词原句、段落句式或固定术语串。

### 18 镜分段

- 01-05：儿童期建立与医院早段
- 06-10：学校 / 家庭 / 告别段
- 11-14：成长、求学、职业建立段
- 15-18：2026 医院段与收束段

## 5. 代码与测试

### 代码改动

- 新增 `liuyi-that-day-v2` 的 fixture 识别与展示名映射。
- 让 fixture 导入器识别新的 authoring 结构，并能从主板资产生成或绑定运行时资产。
- 如果需要，就把主板与派生图的关系写入 asset normalization 层，而不是散在 prompt 里。
- 让新项目的输出前缀、目录名、gate 名称和旧项目完全分离。

### 测试改动

- 新增新项目导入测试，确认 18 镜、资产数量、模型设置和命名都正确。
- 新增资产完整性测试，确认角色 / 场景 / 道具都有主板和派生图。
- 新增 prompt 变更测试，确认 `source/` 与旧项目文本不重合。
- 新增输出门禁测试，确认 final 文件只认新的 `stage3_full_formal_v1` 路径。

### Codex imagegen 413 防线

- 多参考图真实请求必须先过 aggregate payload budget，不只看引用张数。
- Codex handoff 默认使用 `scripts/prepare_codex_imagegen_refs.py` 生成 JPEG safe refs，预算默认且硬上限为 1 MiB 的 prepared JPEG refs，压缩策略改为在预算内优先保真而不是盲目压小。
- handoff 公开 manifest 只暴露 safe refs；原始 PNG 路径只允许存在于 frame spec/fixture 输入，不进入 Codex 内置生图交接面。
- 高一致性镜头可使用 `two_stage_high_consistency` 独立 pack：stage 1 只锁人物与关键道具，stage 2 接入 stage 1 结果后再处理场景、构图与光影。
- `frame_17` 的 6 张真实引用原始总量约 10.6 MiB，base64/JSON 估算约 14.12 MiB；必须使用 handoff 包里的 safe refs，不允许把原始 PNG 直接加载进 Codex 对话。
- 后端 OpenAI-compatible 图编请求同样有 `OPENAI_EDIT_REQUEST_MAX_BYTES` 总预算保护，避免 16 张合法单图叠加成网关 413。

## 6. 完成标准

- 新目录能被导入成独立 project。
- 18 镜能重新出图。
- 每个角色都有完整资产板。
- 产物门禁能区分主板、派生图和最终分镜图。
- 旧项目保留，只作参考，不混入新流水线。

## 6.1 当前完成度（2026-05-09）

- 18 张分镜结果图已齐，`output/codex_image_audit/liuyi-that-day-v2/generated/` 中 `liuyi_frame_01` 到 `liuyi_frame_18` 都已存在。
- `source/01_story_bible.md` 到 `source/05_storyboard_script.md` 已齐，V2 已明确为 canonical。
- 角色必需资产已齐；当前只缺 4 个角色的可选 `head_shot`，不影响基础导入。
- 场景与道具还没补满：当前还缺 42 个 required 资产，主要集中在场景 `board_4k / key_view` 和道具 `board_4k / detail / usage_view`。
- `references/style/liuyi_style_board_v2.png` 与 `references/style/liuyi_storyboard_reference_collage_v2.png` 仍是 planned。
- `liuyi_frame_15` 到 `liuyi_frame_18` 还保留 smoke 产物，可用于对照，不作为正式命名目标。

## 6.2 下一阶段规划

1. 先补当前 18 镜直接用到的一线素材，优先顺序是 `school_playground`、`hospital_room`、`school_gate`、`funeral_hall`、`home_desk`、`exam_admission`、`medical_school`、`doctor_office`、`2026_ward`、`hospital_corridor` 及其对应道具。
2. 再补 `style_board` 和 `storyboard_reference_collage`，把整项目的光线、节奏、人物气质统一到作者级参考层。
3. 保持 `source/05_storyboard_script.md` 为剧情 canonical，只做 cinematic polish，不回写旧版剧情骨架。
4. 补齐素材后，重跑 frame 03 / 17 / 18 的回归，再跑 01-18 全帧，确认直连与 two-stage 两条链路都稳定。
5. 等资产、回归、命名全部稳定后，再把 `project_manifest.draft.json` 升级为正式 `project_manifest.json`。

## 7. 当前落地文件

- [tests/fixtures/story_projects/六一那天_v2/README.md](../tests/fixtures/story_projects/六一那天_v2/README.md)
- [tests/fixtures/story_projects/六一那天_v2/project_manifest.draft.json](../tests/fixtures/story_projects/六一那天_v2/project_manifest.draft.json)
- [tests/fixtures/story_projects/六一那天_v2/references/ASSET_INVENTORY.md](../tests/fixtures/story_projects/六一那天_v2/references/ASSET_INVENTORY.md)
- [tests/fixtures/story_projects/六一那天_v2/source/05_storyboard_script.md](../tests/fixtures/story_projects/六一那天_v2/source/05_storyboard_script.md)
