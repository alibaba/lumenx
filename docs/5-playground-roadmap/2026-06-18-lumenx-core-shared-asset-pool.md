# LumenX Core — 共享资产池(Global Shared Asset Pool)设计 RFC

- 日期:2026-06-18
- 状态:Draft —— 核心决策 + Q1/Q4 已锁;Q2/Q3/Q5 取文档推荐默认(待用户否决)。见 §11。
- 范围:LumenX Core / Studio。Atelier 仅作为未来共享 artifact 的消费方,本轮不涉及。
- 缘起:资产库 Line B UI 收尾时,用户提出"资产不必绑定单个项目——可以有项目无关、任意项目可引用的好资产",并强调要**防止资产膨胀**(策展型而非生成器)。据此单开此设计轮。

---

## 1. 背景与目标

当前资产(Character / Scene / Prop)只存在于某个 **Project** 或 **Series** 内,没有跨项目复用的顶层池。目标:引入一个**项目无关的全局资产池**,让任意项目按需引用其中的精选资产,沉淀可复用的角色/场景/道具库。

设计基调:**库是策展型存储,不是生成器。** 入库一律显式 opt-in,杜绝自动吞入导致的膨胀。

## 2. 已锁定决策(来自需求拷问)

| # | 决策 | 结论 |
|---|---|---|
| D1 | 引用语义 | **活引用 + 三层叠加**:全局存一份,project 按 id 引用;改全局影响所有引用方,project 需要时**本地覆盖**。三层 `global → series → episode`,各层保留自有资产,全局只是额外可引用。**不迁移**现有资产。 |
| D2 | 喂养通道(MVP) | ① 本地图片**导入**;② 从 project/series **提升**;③ Playground 产物**录入**。**不在库里直接生成**。 |
| D3 | 反膨胀 | 入库显式 opt-in,不自动吞;Playground 可生成可不录入。 |
| UI(parked) | 资产库视图/排序 | 工具栏 `按类型↔按项目` 切换(无侧栏,默认按类型);排序加「最近」(真实)+「使用频次」(骨架,待本设计补 usage 字段后转真)。等本设计定库的最终 IA 后一起实现。 |

## 3. 关键发现:Series 就是现成的同型机制(低一级)

整套"共享池"模式在 **Series→Episode** 关系上已经实现,全局池 = **同样的形状多加一层**:

- 引用合并(非拷贝):`resolve_episode_assets(episode, series)` — `pipeline.py:4106-4132`,返回 `episode.X + [a for a in series.X if a.id not in ep_ids]`,episode 本地按 id 优先。
- 单资产查找 + 写路由:`_find_asset_with_source(script, id, type)` — `pipeline.py:903-943`,episode 优先、回退 series,返回 `source` 标签;`_save_after_asset_mutation(source)` `pipeline.py:945-951` 据此存对文件(A2 决策:改共享资产默认落 series,影响所有 episode)。
- 读时合并给前端:`get_project` — `api.py:1080-1133`,episode 资产标 `source="episode"`,series 中本地没有的追加为 `source="series"`(仅出现在响应里,不回写 projects.json)。
- 三级继承先例:`get_effective_prompt`(Episode→Series→system)`pipeline.py:4383-4401`。
- 拷贝/提升先例:`import_assets_from_series`(deepcopy+新 uuid)`pipeline.py:4340-4381`;`reconcile_apply`(episode→series 提升/合并、改写 frame 引用)`api.py:1243+`。

**另一利好**:资产图片二进制早已是**项目无关的扁平命名** `output/assets/{type}/{id}_*.png`(`assets.py:92,215,377,455,546,608`)。故这是**元数据/归属重构,不是存储搬迁,不用搬文件。**

## 4. 数据模型

- 新增全局容器(建议独立存储,见 §11-Q1):一个顶层 `GlobalAssetLibrary { characters: [], scenes: [], props: [] }`,**复用现有 Character/Scene/Prop schema 原样**,持久化到新文件 `output/library_assets.json`(与 `projects.json`/`series.json` 并列,`pipeline.py:60-61` 旁加载)。
- `source` 维度新增第三值 `"global"`(现有为 `"episode"`/`"series"`)。
- Project / Series **不变**:仍按 id 引用,frame 仍存 `scene_id/character_ids/prop_ids`(`models.py:351-353`)。无 schema 迁移。

## 5. 解析与写路由(核心改动,三处"两层→三层")

1. `resolve_episode_assets` `pipeline.py:4106` → 折入全局层(最低优先级):`episode.X` ⊃ `series.X` ⊃ `global.X`,按 id 去重、本地优先。
2. `_find_asset_with_source` `pipeline.py:903` → 增加 `"global"` 回退分支;`_save_after_asset_mutation` `pipeline.py:945` → 增加全局存储落盘目标。
3. `get_project` 读时合并 `api.py:1080` → 追加第三层 `source="global"`(项目本地/series 已有的不重复)。

## 6. 需改造的消费方

**Group B —— 写死"从当前 project 取资产",必须改走 resolver:**
- 批量分镜:`StoryboardGenerator.generate_storyboard` 直接迭代 `script.scenes/characters` — `storyboard.py:30,32`(且 `pipeline.generate_storyboard` `pipeline.py:1513-1519` 没调 resolver)。
- 单帧渲染:`generate_storyboard_render` 用 `script.scenes`/`script.characters` — `pipeline.py:1871,1887`;自动兜底 `storyboard.py:60-125` 只看项目列表。
- 项目级 CRUD:`add_scene` `pipeline.py:890`、`add_uploaded_asset_variant` `pipeline.py:1063-1068`;端点 `api.py:1636/1662/3603`。

**不动(归属无关,已基于已解析的逐帧 URL):** `export.py:16-51`、`merge_videos` `pipeline.py:2652`、`video.py:72-150`。

## 7. 喂养通道实现

### 7.1 本地图片导入
新端点接收上传 → 在全局库建一条对应类型的资产记录,图片落 `output/assets/{type}/`。复用现有 per-asset upload 的落盘约定。

### 7.2 从 project/series 提升
复用 `import_assets_from_series`(`pipeline.py:4340`,deepcopy+新 uuid)与 `reconcile_apply`(`api.py:1243`)的提升模式,目标改为全局库。**活引用语义下**:提升后原项目可改为引用全局那一份(或保留本地副本——见 §11-Q3)。

### 7.3 Playground 录入(管道已铺半截)
现状 `save_to_library` `service.py:99-136` **只把文件拷到 `output/assets/{category}/` 并置 `saved_to_library=True`,不建任何资产记录** → 产物不进任何库、不可被引用,是半成品桩。
- 升级:`save_to_library` 改为**在全局库建一条真正的资产记录**(`category` → 资产类型 character/scene/prop),指向已拷贝的图片。
- 已就位:端点 `POST /playground/history/{gen}/outputs/{out}/save-to-library`(`api.py:81-103`)、`SaveToLibraryRequest{category}`(`models.py:61`)、`PlaygroundOutput.saved_to_library`(`models.py:20`)。
- 策展:录入是显式动作(用户在 Playground 对某产物点"录入资产库"+选类型),默认不录,契合反膨胀。

## 8. API 面

- 新增全局库 CRUD,镜像现有 `/series/{id}/assets`(`api.py:629-808`):`GET/POST/PUT/DELETE /library/assets[...]`。
- 升级 `/playground/.../save-to-library` 为"建全局资产记录"(见 §7.3)。
- `get_project` 响应多一档 `source="global"`(§5.3)。

## 9. 前端

- `AssetLibraryPage.tsx` 从**只读聚合视图**升级为**真实池浏览器**:数据源加 `listLibraryAssets()`,作为 `kind="global"` 的 source。
- 融入已锁的 view-toggle:
  - 「按项目」视图:全局资产单独成组(置顶"全局/共享"组)。
  - 「按类型」视图:全局资产与项目资产一起按类型排,卡片 source 标注"全局"。
- 三通道入口:右上角"导入"(本地)、卡片/抽屉"提升到全局"、Playground 内"录入资产库"。原 mockup 右上角的「导入/新建」按钮**至此有真实落点**。
- R2V `AssetDrawer` 的资产来源列表加入全局池(项目选参考图时可选全局资产)。

## 10. MVP vs 后续

**MVP**:全局库存储 + CRUD + `get_project` 三层读合并 + `resolve_episode_assets` 三层 + Group B 改 resolver + 前端库展示全局 source + **三通道喂养(本地导入 + 从 project/series 提升 + Playground 录入升级)**。
**后续**:fork-on-use(引用转本地副本)、删除/引用完整性、去重、视频类资产、**usage 计数**(顺带把 parked 的「使用频次」排序从骨架转真)。

## 11. 待定决策(请 review)

- **Q1 全局存储形态 — ✅ 已定**:独立 `library_assets.json` + `GlobalAssetLibrary` 容器(与 series 解耦,边界清晰,未来迁 Core 更顺)。
- **Q2 删除/引用完整性**:全局资产被项目引用时删除如何处理——引用时禁止硬删 + 提示(**推荐 MVP 最小:有引用则警告并阻止硬删**)/ 软删 + 标记 / 允许删除容忍悬挂引用。
- **Q3 提升后原项目**:提升到全局后,原项目改为**引用全局**(去重)还是**保留本地副本**(独立)?(活引用下推荐:默认引用全局,本地按需覆盖。)
- **Q4 MVP 通道取舍 — ✅ 已定**:三通道(导入 + 提升 + Playground 录入)全进 MVP。提升复用 `import_assets_from_series`/`reconcile_apply` 模式。
- **Q5 命名**:UI 标签用「全局资产 / 共享库 / Library」哪个。

## 12. 风险与回滚

- 风险集中在 §5/§6 的"两层→三层"与 Group B 改造;export/video 不受影响,回归面可控。
- 全程 additive、不迁移:全局库为空时,所有现有行为与今天**逐位一致**,可灰度上线。
- 二进制无需搬迁(扁平 id 命名),降低数据风险。
