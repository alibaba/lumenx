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

## 6. 完成标准

- 新目录能被导入成独立 project。
- 18 镜能重新出图。
- 每个角色都有完整资产板。
- 产物门禁能区分主板、派生图和最终分镜图。
- 旧项目保留，只作参考，不混入新流水线。

## 7. 当前落地文件

- [tests/fixtures/story_projects/六一那天_v2/README.md](../tests/fixtures/story_projects/六一那天_v2/README.md)
- [tests/fixtures/story_projects/六一那天_v2/project_manifest.draft.json](../tests/fixtures/story_projects/六一那天_v2/project_manifest.draft.json)
- [tests/fixtures/story_projects/六一那天_v2/references/ASSET_INVENTORY.md](../tests/fixtures/story_projects/六一那天_v2/references/ASSET_INVENTORY.md)
- [tests/fixtures/story_projects/六一那天_v2/source/05_storyboard_script.md](../tests/fixtures/story_projects/六一那天_v2/source/05_storyboard_script.md)
