# R2V Workflow v2 · 系列级架构重构设计方案

> **Status**: ✅ Design approved · Pending implementation
> **Date**: 2026-05-23
> **Author**: 多轮 grill-me session（用户 + Claude）
> **Scope**: LumenX Studio R2V workflow 从"项目内部 4 step"重构为"系列优先 5 step + 系列层风格 + 无剧本模式预留"

---

## 1. 背景与动机

### 1.1 当前问题

`v1`（current production）的根本问题是 **基于"单一 project"思考产品架构**，没有充分利用系列（Series）这一层：

1. **风格定调 (Step 2) 在每集重复定义** —— Series 已有 `art_direction` 字段但**无 UI 入口**，每集 Project 都在 Style step 里重新定义一套，违反"一系列风格统一"的创作直觉。
2. **角色资产跨集无去重** —— 第 2 集脚本解析时不会智能链接已有角色，导致系列库重复条目。
3. **PropertiesPanel 是早期设计遗留** —— 全局右栏 256px 显示"上下文/项目统计/Art风格"，三项都有更好去处。
4. **Cast / 本集素材 视图缺失** —— 用户无法在一处看到"这一集到底用了哪些角色/场景/道具"。
5. **不支持无剧本创作流** —— 现工作流默认"先剧本 → 后分镜"，无法支持手头已有素材直接从分镜开始的用户。

### 1.2 设计哲学

- **Series 优先**：所有可共享的创作配置（风格、角色、场景、道具）以 Series 为真相所在地
- **Project = Episode 视角**：每集是系列的一个时间切片，呈现该集的"本集视角"
- **Inherit + Override**：每集默认继承系列基线，按需手动 override（带摩擦避免误改）
- **Reconcile by intent**：跨集资产去重通过 AI 推荐 + 用户一键确认，避免静默错配

---

## 2. 决策清单（grill 结果）

### Q1 · 风格权威所在地: **B · Series 默认 + Project override**
- `series.art_direction` 是真相基线
- `project.art_direction` 为空 → inherit series；非空 → override
- "破例风格"通过 Step 2 解锁编辑实现

### Q2 · Project 内 Step 2 形态: **A · 默认 read-only + "解锁编辑"按钮**
- 进入时显示 `📌 继承系列：Ghibli Magic [解锁编辑]`
- 解锁后变完整编辑器 + 顶部 banner `⚠ overridden [重置为系列]`
- override 是**有意识动作**，避免无意识 drift

### Q3 · Step 序列: **A · 5 steps**
```
Script · Style · Cast · Storyboard · Assembly
```
（新增 Cast 作为"本集素材"step；Style 保留并支持 override）

### Q4 补充 · 角色定义模型: **persona 字段**
- Character 本质 = 视觉特征 + 音色特征
- "小时候张三" / "长大张三" / "穿西装张三" 是 3 个 character
- 通过 `persona: str` 字段关联同一"人"的多个视觉变体（v1 加 schema，v2 加 UI 分组）

### Q5 · Series 层 Style 入口: **A · SeriesDetailPage sidebar 加 "Art Direction" item**
- 与 Characters / Scenes / Props / Episodes 并列
- ArtDirection 组件支持 `level="series" | "project"` prop

### Q6 · Reconcile 弹窗时机: **A2 · 解析完立即弹 + 默认接受推荐 + 一键确认**
- 时机：Script step "提取实体" 完成时
- 状态：默认全选系统推荐，低置信度高亮黄
- "[查看并修改]" / "[全部确认]" 两个按钮
- **Q6.1 拒绝匹配**: 该角色作为**新角色加入系列库**（同名也创建，persona 留空）

### Q7 · PropertiesPanel 命运: **删除 + 改造 Script step 右栏为"上回书说到"**
- PropertiesPanel 整体删除（早期设计遗留）
- Script step 右栏改为 **"上回书说到"** 面板：上一集摘要 / 原文片段
- 用户灵感：连续剧作者第 4 集开头最经常忘"上一集结尾发生了什么"

### Q7-followup · "上回书说到" v1 scope: **双视图 + 按需触发**
- **标题**：中文 "上回书说到" / 英文 "Previously on..."（i18n 双语自动切换）
- **原文片段**（默认即时显示）：上一集结尾 500-800 字，零 LLM 调用、零成本
- **AI 概要**（按需触发）：用户点 `[生成 AI 概要]` 按钮才调 qwen3.6-plus 生成 200 字摘要
  - 避免静默消耗用户的 LLM 配额
  - 用户决定 "是否需要 AI 帮我总结"
  - 生成后 cache，下次进 step 显示已有概要 + `[刷新]` 按钮
- Cache + invalidate（上一集脚本 revision 变化 → 概要标过期 + "刷新概要"）
- 第 1 集显示 `第 1 集 · 故事的开端`（中文）/ `Episode 1 · The beginning` (英文)
- **未来扩展**：跨 step 延展 / 双向预览 / Character callback 助手 / 用户手动编辑概要

### Q8 · Cast 与系列层 / ConsistencyVault 分工: **A · Cast = 本集视角**
| 层 | 职责 |
|---|---|
| SeriesDetailPage (系列层) | CRUD source of truth — 全系列 character/scene/prop 管理 |
| Cast step (项目层) | 本集 read-only filter view + 本集新角色生成参考图 |
| ConsistencyVault | 留给 i2v_legacy 老 workflow，R2V 不引用 |

### Q9 · Cast step 内部布局: **A · 三 section 平铺**
```
📍 本集角色 (5)
   [card] [card] [card] ...
🎬 本集场景 (3)
   [card] [card] ...
📦 本集道具 (8)
   [card] [card] [card] ...
```
每个 card：缩略图 + 名字 + 出场次数 chip + 状态徽（`✓ 参考图就绪` / `⚠ 待生成` / `🆕 本集新角色`）

### Q10 · Script step entities panel: **删除**
- 原 layout: `[main editor] + [entities panel 360px] + [PropertiesPanel 256px]`
- 新 layout: `[main editor] + ["上回书说到" 右栏 ~320px]`
- entities 全权交给 Cast step

### Q10.1 · 提取实体后跳转: **b · reconcile 弹窗带"去 Cast 查看 →" 按钮**
- 用户主动跳转，不强制 navigate

### Q11 · 现有数据迁移到 inherit 模式: **B · 进 Style step 时一行 promote 按钮**
- 当 `series.art_direction` 为空且 `project.art_direction` 非空：
  - 显示 `🎨 此风格尚未设为系列基线 [推广为系列基线]`
- 用户点按钮 → promote 项目级 → 系列级
- promote 后 `project.art_direction` 保留（如有修改 → overridden）

### Q12 · 无剧本工作流 (content_mode): **总体方案通过**
- 创建系列时选 radio：`◉ 我有剧本 (scripted) / ○ 我直接创作 (freeform)`
- 新增 `series.content_mode: Literal["scripted", "freeform"]`，与 workflow_mode 正交
- Step 序列差异：
  - **scripted**: Script · Style · Cast · Storyboard · Assembly (5 steps)
  - **freeform**: ~~Script~~ · Style · Cast · Storyboard · Assembly (4 steps)
- freeform 模式 Cast step 支持直接 `+ 新角色`（AI 生成 / 上传图片 两个 tab）
- **v1 不支持事后切换模式**（数据迁移复杂）
- **v2+**: 同系列混合模式 / Notes step

### Q12 补充 · 角色参考图形态: **A · 单 master sheet**
- v1: `character.reference_sheet: AssetUnit`（一张含多视角的 character sheet，或单 portrait 也行）
- legacy `full_body / three_views / head_shot` 三个 AssetUnit 标 `[DEPRECATED]`
- 数据迁移：legacy `full_body` 焦用为 `reference_sheet`（最少 schema 变化）
- AI 生成默认 prompt 含 "character sheet style"
- 用户也可上传任意单图

---

## 3. 数据 Schema 变化

### 3.1 Series 新增字段
```py
class Series(BaseModel):
    # 新增
    content_mode: Literal["scripted", "freeform"] = "scripted"
    # 已有但需要 UI 入口
    art_direction: Optional[ArtDirection]  # 现在有 UI 编辑了
```

### 3.2 Character 新增字段
```py
class Character(BaseModel):
    # 新增
    persona: str = ""  # 关联同一"人"的多个视觉变体；v1 schema only, v2 UI 分组
    reference_sheet: Optional[AssetUnit]  # 新主字段（v1 = legacy full_body 焦用）

    # legacy 标记 deprecated（仍保留兼容）
    full_body: Optional[AssetUnit]       # [DEPRECATED v2 → reference_sheet]
    three_views: Optional[AssetUnit]     # [DEPRECATED v2]
    head_shot: Optional[AssetUnit]       # [DEPRECATED v2]
```

### 3.3 ArtDirection inherit/override 模型
**不需要新字段** —— 利用现有 `series.art_direction` + `project.art_direction`：
```
effective_art_direction(project) = project.art_direction || series.art_direction
state(project) =
    "no_series"    if project.series_id is None
    "inherit"      if project.art_direction is None
    "overridden"   if project.art_direction != series.art_direction
```

### 3.4 Episode summary（"上回书说到"）
```py
class Script(BaseModel):
    # 新增
    last_episode_summary_cache: Optional[str]  # AI 概要（200 字）
    last_episode_summary_revision: Optional[int]  # 上一集脚本 revision，用于 invalidate
```

---

## 4. UI/UX 设计细节

### 4.1 SeriesDetailPage Sidebar
```
本系列 · N 集 (header)
─────────────
🎨 ART DIRECTION       ← 新增
─────────────
👤 Characters (12)
🎬 Scenes (8)
📦 Props (15)
─────────────
EPISODES
  第 1 集 ▾
  第 2 集
  ...
```

### 4.2 Step 序列 + ProjectClient 路由
```ts
const steps = useMemo(() => {
  const base = ["script", "style", "cast", "storyboard_r2v", "assembly"];
  if (series?.content_mode === "freeform") return base.filter(s => s !== "script");
  return base;
}, [series?.content_mode]);
```

### 4.3 Script step 新 layout
```
┌────────────────────────────┬──────────────────────────┐
│ StepHeader (Script)         │  上回书说到              │
├────────────────────────────┤  Previously on...        │
│                             │  ─────────              │
│  [Script textarea]          │  [AI 概要]              │
│                             │  · 空: [✨ 生成 AI 概要]│
│                             │  · 已生成: 概要文本     │
│                             │    + [🔄 刷新]           │
│                             │  ─────────              │
│                             │  原文片段 (尾部 500 字)  │
│                             │  [默认即时显示，零成本] │
└────────────────────────────┴──────────────────────────┘
```

**关键交互细节**：
- **标题双语**：中文 "上回书说到" + 英文小标题 "Previously on..."；中英文模式都显示（中文为主标题、英文为副标题）
- **AI 概要按需触发**：进入 step 不自动调 LLM；用户点 `[✨ 生成 AI 概要]` 按钮才调 qwen3.6-plus
  - 理由：避免静默消耗用户配额；尊重用户"是否要 AI 帮我总结"的主动权
- **原文片段默认即时显示**：零 LLM、零等待
- **Cache 失效**：上一集 revision 变化 → 概要旁出现 `⚠ 上一集已更新 [刷新]`
- **第 1 集状态**：右栏显示 `Episode 1 · The beginning` placeholder，AI 概要 / 原文片段都不渲染

### 4.4 Style step 新 UX
**Inherit 状态（默认）**:
```
┌──────────────────────────────────────────┐
│ StepHeader (Style)                       │
├──────────────────────────────────────────┤
│ 📌 继承系列：Ghibli Magic   [解锁编辑]   │
│                                          │
│ [系列风格预览 read-only display]         │
│                                          │
└──────────────────────────────────────────┘
```

**Override 状态（解锁后）**:
```
┌──────────────────────────────────────────┐
│ StepHeader (Style)                       │
├──────────────────────────────────────────┤
│ ⚠ 已 override 系列基线  [重置为系列]    │
│                                          │
│ [完整编辑器: 预设 + AI 推荐 + 编辑器]    │
│                                          │
│ Bottom sticky: [应用并继续 →]            │
└──────────────────────────────────────────┘
```

**Promote 状态（系列基线为空）**:
```
┌──────────────────────────────────────────┐
│ 🎨 此风格尚未设为系列基线               │
│    [推广为系列基线]                      │
│                                          │
│ [项目级 art_direction 编辑器]            │
└──────────────────────────────────────────┘
```

### 4.5 Cast step 完整 layout
```
┌─────────────────────────────────────────────┐
│ StepHeader (Cast)                           │
├─────────────────────────────────────────────┤
│ 📍 本集角色 (5)                  [+ 新角色] │
│ ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐         │
│ │... │ │... │ │... │ │... │ │🆕  │         │
│ │ ✓  │ │ ✓  │ │ ✓  │ │ ⚠  │ │待生成│       │
│ └────┘ └────┘ └────┘ └────┘ └────┘         │
│                                             │
│ 🎬 本集场景 (3)                              │
│ [grid ...]                                  │
│                                             │
│ 📦 本集道具 (8)                              │
│ [grid ...]                                  │
└─────────────────────────────────────────────┘
```

每个 character card 上的状态徽：
- `✓ 参考图就绪`：reference_sheet 已生成
- `⚠ 待生成`：本集引用但参考图 missing
- `🆕 本集新角色`：reconcile 时确认为新角色（最高 attention，红边）

点击 card：
- 已就绪 → 弹 detail modal（参考图大图 + 本集出场 frame 列表 + 跳转）
- 待生成 → 弹 generation modal（AI 生成 prompt 编辑 / 上传图片 两 tab）

### 4.6 Reconcile 弹窗
```
┌─────────────────────────────────────────────┐
│  脚本已解析 · 已识别 5 个角色                │
│  ┌───────────────────────────────────────┐  │
│  │ ✓ 小明  → 系列·小明 (96% 匹配)         │  │
│  │ ✓ 李四  → 系列·李四 (89% 匹配)         │  │
│  │ ⚠ 王五  → 系列·王老五? (62% 匹配)      │  │
│  │           [作为新角色] [手动选择]      │  │
│  │ 🆕 张三 → 新角色 (系列库无匹配)        │  │
│  │ 🆕 赵六 → 新角色 (系列库无匹配)        │  │
│  └───────────────────────────────────────┘  │
│                                              │
│  [查看并修改]  [全部确认]  [去 Cast 查看 →] │
└─────────────────────────────────────────────┘
```

---

## 5. Phasing 实施计划

### Phase 1 · 局部立竿见影（~1 天）
**目标**：清理过时 UI + 搭建 Step 5 骨架，不破坏现有数据
- 删除 PropertiesPanel（全局）
- Script step 删除内嵌 entities panel
- Step 序列 4 → 5（插入 Cast step 占位）
- Cast step 三 section 基本结构（read-only 显示 frames 引用的 character/scene/prop，无 reconcile）
- StepHeader stepNumber 1-5 调整 + i18n keys（cast.*）

### Phase 2 · Series Art Direction（~1 天）
**目标**：系列层风格 UI + Style step inherit/override
- SeriesDetailPage sidebar 加 "Art Direction" item
- ArtDirection 组件加 `level="series" | "project"` prop（重用现有组件）
- Series.art_direction 编辑 + 持久化
- Style step (project level) 默认 read-only inherit / 解锁后 override
- Q11 promote 提示按钮

### Phase 3 · "上回书说到"（~1 天）
**目标**：Script step 右栏新功能
- 后端新增 `GET /projects/{id}/previous_episode_summary` 接口（qwen3.6-plus 生成 + cache）
- Script step 右栏渲染 "上回书说到" 面板
- AI 概要折叠 + 原文片段 + 刷新按钮
- Cache invalidate logic（上一集 revision 变化）

### Phase 4 · 跨集 Reconcile（~2-3 天）
**目标**：智能资产去重
- Character 新增 `persona` 字段（schema only）
- 后端"提取实体"接口返回时增加匹配建议（name fuzzy + description embedding）
- 前端 reconcile modal（弹窗 / 默认全选 / 拒绝 = 新角色）
- "去 Cast 查看 →" 跳转 + 触发 navigation
- Cast step 新角色徽

### Phase 5 · Cast step 完整能力（~2 天）
**目标**：Cast step 闭环
- Cast step 新角色 `+` 按钮：`[AI 生成]` / `[上传图片]` 双 tab
- 参考图状态徽（ready / missing / new）
- 点击 cast → 弹 detail / generation modal
- Character schema 走单 `reference_sheet`（legacy `full_body` 焦用）
- 数据迁移代码（旧 character 读取兼容）

### Phase 6 · Freeform 模式（future, ~2-3 天）
**目标**：无剧本工作流
- Series.content_mode 字段
- 创建系列 modal 加 radio
- ProjectClient 根据 content_mode 渲染不同 step 序列
- freeform 模式 Cast step 直接创建/上传角色（其实 Phase 5 已经做了）

---

## 6. Future Todos

| 项目 | Phase | 说明 |
|------|-------|------|
| Character persona UI 分组 | v2 | Cast step 按 persona 分组显示同一"人"的多个视觉变体 |
| "上回书说到" 跨 step 延展 | v2 | Storyboard step 也有 "上一集结尾分镜" 参考 |
| "上回书说到" 双向预览 | v2 | AI 基于当前结尾给"下一集开头 hook 预测" |
| Character callback 助手 | v2 | 右栏支持 @ 角色 → 展开角色出场摘要 |
| 用户手动编辑概要 | v2 | "上回书说到" AI 概要支持 override |
| Reference sheet auto-crop | v2 | master sheet 自动 crop 出 head_shot/full_body view |
| Freeform Notes step（可选） | v2 | freeform 模式下可启用 Notes step 记录创作意图 |
| 同系列混合模式 | v3 | 部分 episode scripted 部分 freeform |
| Character 音色绑定 | v2 | persona / character 关联 voice_id |
| 模式事后切换 | v3 | series content_mode 切换工具（数据迁移复杂） |

---

## 7. 实施前必读约束

- **不破坏 i2v_legacy workflow**：ConsistencyVault 保留，新框架只影响 R2V workflow
- **不破坏现有数据**：旧 character 的 full_body/three_views/head_shot 仍可读，渐进迁移到 reference_sheet
- **每个 Phase 可独立 ship**：用户能看到渐进改善，不需要等全做完
- **i18n 双语**：新增 step / 提示 / 按钮文案都要 zh.json + en.json
- **typecheck 必过**：每个 Phase 完成时 `npm run typecheck` 必须通过

---

## 8. 改动文件预估

### Backend
- `src/apps/comic_gen/models.py`：Character.persona、Character.reference_sheet、Series.content_mode、Script.last_episode_summary_*
- `src/apps/comic_gen/api.py`：previous_episode_summary 接口、提取实体接口增加匹配建议
- `src/apps/comic_gen/pipeline.py`：character 匹配算法、概要生成 + cache 逻辑

### Frontend
- `frontend/src/components/project/ProjectClient.tsx`：5 step 序列、删除 PropertiesPanel、freeform mode 分支
- `frontend/src/components/modules/ScriptProcessor.tsx`：删除 entities panel + 右栏改 "上回书说到"
- `frontend/src/components/modules/ArtDirection.tsx`：加 `level` prop + inherit/override UX
- `frontend/src/components/modules/Cast.tsx`：**新组件** —— 三 section 平铺 + reconcile 后跳转目标
- `frontend/src/components/modules/PreviousEpisodeSummary.tsx`：**新组件** —— Script step 右栏
- `frontend/src/components/modules/ReconcileModal.tsx`：**新组件** —— 跨集资产 reconcile
- `frontend/src/components/series/SeriesDetailPage.tsx`：sidebar 加 Art Direction item
- `frontend/src/components/series/SeriesSidebar.tsx`：sidebar item kind 扩展
- `frontend/src/app/page.tsx`：创建系列 modal 加 content_mode radio
- `frontend/messages/{zh,en}.json`：新 step + 新组件 i18n keys
- `frontend/src/components/shared/StepHeader.tsx`：stepNumber 默认上限 5（已支持任意值）

---

## 9. 验收 criteria（per Phase）

每个 Phase ship 时应满足：
- `npm run typecheck` 通过
- `npm run test` 通过（如有）
- 浏览器实测主流程不 regression
- 关键 UI 改动有 screenshot 记录在 PR 描述
- 中英文切换都能正确展示
