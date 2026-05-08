# 六一那天_v2 资产清单

状态：draft。
这份清单只定义新项目的资产包，不代表真实 PNG 已经生成。

## 资产包规则

- 角色：每人一张 4K 主板，派生 `full_body`、`three_view`、`expression_sheet`，成年或病人角色追加 `head_shot`。
- 场景：每个场景一张 4K 主板，派生 `key_view`，高频场景追加光线或时间变体。
- 道具：每个道具一张主板，派生 `detail`，会被手持或递交的道具追加 `usage_view`。
- 运行时导入只绑定派生图；主板作为作者级参考，不直接当最终分镜输出。

## 角色

| 资产 ID | 主板 | 必需派生图 | 可选派生图 | 重点 |
| --- | --- | --- | --- | --- |
| `liuyi_char_xiaoqi_child_v2` | `characters/liuyi_char_xiaoqi_child_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | 无 | 儿童小琪，演出服、等待、失落、强忍 |
| `liuyi_char_mother_v2` | `characters/liuyi_char_mother_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | `head_shot` | 疲惫温柔，克制哭意，校门和病房连续 |
| `liuyi_char_father_v2` | `characters/liuyi_char_father_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | `head_shot` | 病中消瘦，礼物托付，温柔虚弱 |
| `liuyi_char_xiaoqi_young_v2` | `characters/liuyi_char_xiaoqi_young_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | 无 | 少年小琪，学习压力，医学院过渡 |
| `liuyi_char_xiaoqi_adult_v2` | `characters/liuyi_char_xiaoqi_adult_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | `head_shot` | 成年医生，小琪身份延续，专业又柔软 |
| `liuyi_char_boy_v2` | `characters/liuyi_char_boy_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | 无 | 2026 小男孩，儿童节演出服，期待父亲 |
| `liuyi_char_boy_father_v2` | `characters/liuyi_char_boy_father_v2/*_board_4k.png` | `full_body` / `three_view` / `expression_sheet` | `head_shot` | 2026 病房父亲，虚弱但清醒 |

## 场景

| 资产 ID | 主板 | 必需派生图 | 可选派生图 | 重点 |
| --- | --- | --- | --- | --- |
| `liuyi_scene_school_playground_v2` | `scenes/liuyi_scene_school_playground_v2/*_board_4k.png` | `key_view` | `morning_warm` | 2008 六一操场，舞台、彩旗、跑道 |
| `liuyi_scene_school_gate_v2` | `scenes/liuyi_scene_school_gate_v2/*_board_4k.png` | `key_view` | `after_show` | 演出后校门，人群散去 |
| `liuyi_scene_hospital_room_v2` | `scenes/liuyi_scene_hospital_room_v2/*_board_4k.png` | `key_view` | `cool_day` | 2008 普通病房，床头柜、白熊、冷光 |
| `liuyi_scene_2026_ward_v2` | `scenes/liuyi_scene_2026_ward_v2/*_board_4k.png` | `key_view` | `childrens_day_daylight` | 2026 肝病科病房，气球与童年呼应 |
| `liuyi_scene_hospital_corridor_v2` | `scenes/liuyi_scene_hospital_corridor_v2/*_board_4k.png` | `key_view` | `quiet_morning` | 医院走廊，收束镜头 |
| `liuyi_scene_funeral_hall_v2` | `scenes/liuyi_scene_funeral_hall_v2/*_board_4k.png` | `key_view` | 无 | 克制灵堂，不阴森 |
| `liuyi_scene_home_desk_v2` | `scenes/liuyi_scene_home_desk_v2/*_board_4k.png` | `key_view` | `night_lamp` | 家中书桌，台灯、旧物、白熊 |
| `liuyi_scene_exam_admission_v2` | `scenes/liuyi_scene_exam_admission_v2/*_board_4k.png` | `key_view` | 无 | 录取通知书与白熊同框 |
| `liuyi_scene_medical_school_v2` | `scenes/liuyi_scene_medical_school_v2/*_board_4k.png` | `key_view` | 无 | 医学院实验楼或教室 |
| `liuyi_scene_doctor_office_v2` | `scenes/liuyi_scene_doctor_office_v2/*_board_4k.png` | `key_view` | 无 | 成年小琪办公场景 |

## 道具

| 资产 ID | 主板 | 必需派生图 | 可选派生图 | 重点 |
| --- | --- | --- | --- | --- |
| `liuyi_prop_white_bear_v2` | `props/liuyi_prop_white_bear_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 白色毛绒小熊，贯穿物件 |
| `liuyi_prop_paper_bag_v2` | `props/liuyi_prop_paper_bag_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 父亲礼物纸袋 |
| `liuyi_prop_child_drawing_v2` | `props/liuyi_prop_child_drawing_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 小琪的儿童画，后期叠字 |
| `liuyi_prop_childrens_day_balloons_v2` | `props/liuyi_prop_childrens_day_balloons_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 2026 儿童节气球 |
| `liuyi_prop_medical_textbooks_v2` | `props/liuyi_prop_medical_textbooks_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 医学教材与病历夹 |
| `liuyi_prop_father_memorial_portrait_v2` | `props/liuyi_prop_father_memorial_portrait_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 父亲黑白遗照，避免模型乱字 |
| `liuyi_prop_family_photo_v2` | `props/liuyi_prop_family_photo_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 父女旧合照 |
| `liuyi_prop_notebook_pencil_v2` | `props/liuyi_prop_notebook_pencil_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 复习笔记和铅笔 |
| `liuyi_prop_admission_notice_v2` | `props/liuyi_prop_admission_notice_v2/*_board_4k.png` | `detail` / `usage_view` | 无 | 录取通知书，文字后期处理 |

## 验收口径

- 资产完整性：每个角色必须有主板和 3 个必需派生图；每个场景 / 道具必须有主板和必需派生图。
- 运行时兼容：正式 manifest 生成后，`asset_packages` 应能扁平化为 `reference_assets`。
- 输出隔离：v2 最终分镜只落 `output/codex_image_audit/liuyi-that-day-v2/generated/`。
- 边界：这里不是旧 fixture 回放，不引用 `output/codex_image_audit/liuyi-that-day/generated/` 作为正式交付。
