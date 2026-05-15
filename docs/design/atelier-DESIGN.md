# LumenX Atelier — DESIGN.md

> **Status**: v0.3 · Media-as-node + Composer + Selection Action Bar (2026-05-15)
> **Changelog v0.3**: 借鉴 LibTV / RHTV 等成熟无限画布产品。**节点 = 媒体本体**（不再是装媒体的卡）；**单一 Composer 浮窗** 接管所有生成配置（不再有 v0.2 的 340w Inspector）；**Selection Action Bar** 浮在选中节点上方承载操作。Right Rail 删掉 Node tab，改用 minimap 做导航。Atelier 的差异化保留：persistent **DraftNode**（240×~80 超薄卡）承载 PRD §10.7 "3 drafts 并排评审"。
> **Changelog v0.2**: 把 Video Node 拆成 Compact Card + Floating Inspector。**已被 v0.3 取代**。
> **Status (legacy)**: v0.1 Variant baseline / v0.2 compact card+inspector 都已废弃，仅作为变更轨迹保留
> **Scope**: LumenX Atelier only. Studio is out of scope.
> **Source of truth precedence**: 本 DESIGN.md > Variant mockup（基线）> 临时讨论。
> **关系**: PRD 说"做什么、要满足什么验收"，DESIGN.md 说"长什么样、用哪些 token、怎么排版交互"。冲突时本 DESIGN.md 服从 PRD 的产品定义，但 PRD 不规定视觉细节。

---

## 1. Purpose & Scope

### 1.1 文档存在的理由

Atelier 是 graph-first 个人创作壳，与 Studio 的 pipeline-first 视觉信息架构完全不同。**Studio 的设计规范不能被复用到 Atelier**——Studio 是表单/列表/分镜密度，Atelier 是空间/媒体/对话密度。本 DESIGN.md 把 Atelier 的视觉 + 交互语言独立沉淀，让前端实现 agent 拿到这份文档与配套 hi-fi 原型即可开干，不再反复对齐口味。

### 1.2 范围

DESIGN.md 覆盖：
- 视觉 token（颜色、字体、间距、半径、阴影、动画）
- 五区 IA 与各区尺寸
- 组件模式（Toolbar Capsule、Floating Inspector、Right Rail、Sequence Strip、Approval Card 等）
- 节点视觉规格（5 类节点 + 候选卡 + 选中态）
- Agent Panel 深度规格（对话、审批、权限模式、工具时间线、Composer）
- Canvas 交互 + 连接线视觉
- 状态视觉矩阵（一表覆盖所有 status union）
- 空态、错误态、加载态
- 反例（do-not）
- 可访问性与响应式
- 评审签收清单

DESIGN.md 不覆盖：
- 后端契约、API 设计、状态机持久化（见 PRD + roadmap）
- 模型目录内容（见 `frontend/src/lib/modelCatalog.ts`）
- Studio 视觉
- 长期商业化、社区、协作的视觉（v1 非目标）

### 1.3 引用的同类源

| 文档 | 角色 |
|---|---|
| `docs/agents/deliverables/atelier-frontend-redesign-prd.md` | 26 节产品需求与验收（PRD） |
| `docs/plans/2026-05-08-lumenx-studio-atelier-core-roadmap.md` | 产品家族 roadmap |
| `docs/plans/2026-05-08-atelier-v1-implementation-boundary.md` | v1 边界与节点类型清单 |
| `docs/plans/2026-05-09-atelier-agent-runtime-implementation-plan.md` | Agent runtime 与 approval 模型 |
| `frontend/src/app/globals.css` | 已有 CSS variables、`glass-*` 工具类、`btn-tip` tooltip |
| `frontend/tailwind.config.ts` | 已有 Tailwind alias |
| `frontend/src/components/atelier/AtelierShell.tsx` | 当前实现的 className 范本（被本文档大量引用） |
| `frontend/src/components/atelier/AgentPanelTrace.tsx` | Session/Readiness/History 子面板范本 |
| `frontend/src/lib/api.ts` | 状态 union 字面量（不可偏离） |

---

## 2. Brand Voice for Atelier

### 2.1 Atelier 子语调

LumenX 总品牌人格是 **Creative · Immersive · Geeky**。Atelier 在此之上收敛为：

> **Creator's quiet cockpit · Spatial sketchbook**
>
> 安静的创作驾驶舱 · 空间化的速写本

含义：
- **Quiet** — 默认零装饰；只有用户操作时控件才"活过来"。背景是黑色画布，控件是玻璃，内容（图、视频、文字）是发光体。
- **Cockpit** — 控件少而精，每个控件都有明确职责；不暴露原始 JSON、不暴露执行图细节。
- **Sketchbook** — 节点可以随手摆放，分支随时可建，废稿不羞耻。鼓励"脏一点的探索"，不是"整洁的工作流"。

### 2.2 三条反例（do-not）

1. **Don't be ComfyUI** — 不暴露 port、edge type label、node parameter dump、原始 graph 结构作为默认体验。Atelier 用语义节点（idea / image / video / audio / sequence），不是执行原语。
2. **Don't be Figma** — 不要 4 块以上侧边面板、不要工具栏满铺、不要"右键菜单是主交互"。Atelier 的主交互是画布 + 一个右侧栏 + 一个底部条，floating inspector 在节点旁出现。
3. **Don't be Marketing Hero** — 空画布不放大字号 slogan、不放渐变 orb、不放 CTA 巨按钮。空画布是"空速写本"，给一个 **dotted grid + 极简提示文字（≤ 16px）** 即止。

---

## 3. Design Tokens

> **总原则**：所有 token 一律引用 `frontend/src/app/globals.css` 已有的 CSS variable 或 `frontend/tailwind.config.ts` 已有的 alias。**不创造新色板**；如必须扩展，在本节末尾声明并由实现 PR 同步入 `globals.css`。

### 3.1 颜色

**画布与表面（语义层）**

| Token | dark 值 | light 值 | 用途 |
|---|---|---|---|
| `bg-background` (`--color-bg-base`) | `#050508` | `#f8f9fa` | 画布最底色 |
| `bg-surface` (`--color-bg-surface`) | `rgba(0,0,0,0.4)` | `#fff` | 浮层默认底（toolbar / agent panel / sequence strip） |
| `bg-elevated` (`--color-bg-elevated`) | `#1a1a1a` | `#fff` | 节点容器底（深一档，与画布拉开层级） |
| `bg-input-bg` (`--color-bg-input`) | `rgba(0,0,0,0.3)` | `#f1f3f5` | textarea/select 内底 |
| `bg-hover-bg` (`--color-bg-hover`) | `rgba(255,255,255,0.05)` | `rgba(0,0,0,0.04)` | 按钮 hover、菜单 hover |
| `bg-glass` (`--color-glass`) | `rgba(255,255,255,0.05)` | `rgba(0,0,0,0.03)` | 玻璃面板默认底 |
| `border-glass-border` (`--color-border-default`) | `rgba(255,255,255,0.10)` | `rgba(0,0,0,0.10)` | 分割与边框默认 |
| `border-border-subtle` (`--color-border-subtle`) | `rgba(255,255,255,0.05)` | `rgba(0,0,0,0.06)` | 极弱分割（节点内的细分） |
| `text-foreground` (`--color-text-primary`) | `#ededed` | `#1a1a1a` | 主要文字 |
| `text-text-secondary` (`--color-text-secondary`) | `#9ca3af` | `#6b7280` | 次要文字、表单 label |
| `text-text-muted` (`--color-text-muted`) | `#6b7280` | `#9ca3af` | uppercase 标签、hint、metadata |
| `bg-overlay` (`--color-overlay`) | `rgba(0,0,0,0.8)` | `rgba(0,0,0,0.2)` | modal 蒙层 |
| `bg-surface-inset` (`--color-bg-inset`) | `rgba(255,255,255,0.03)` | `rgba(0,0,0,0.02)` | 节点内 inset 区（如候选 grid 容器） |

**强调色（不分主题）**

| Token | 值 | 用途 |
|---|---|---|
| `text-primary` / `bg-primary` | `#646cff` | 选中描边、激活按钮、Agent action 高亮、连接线已建立态 |
| `text-secondary-color` / `bg-secondary` | `#535bf2` | 较少使用；hover 加深 |
| `text-accent` / `bg-accent` | `#ff0080` | 仅限品牌时刻 / 强调瞬间（generate CTA 不用此色，避免误用） |

**状态色组**（直接复用 `AtelierShell.tsx:73-76, 107-110`，**不可偏离**）

| 状态 | className 范本 | 备注 |
|---|---|---|
| completed | `border-emerald-400/50 bg-emerald-400/10 text-emerald-200` | 节点 / 候选 / agent turn 完成 |
| processing / pending | `border-blue-400/50 bg-blue-400/10 text-blue-200` | 进行中（含 `pending`） |
| failed / denied | `border-red-400/50 bg-red-400/10 text-red-200` | 失败、被拒 |
| approval_required / waiting_approval | `border-amber-300/40 text-amber-100` | 等待用户决断 |
| draft / idle | `border-white/15 bg-white/8 text-text-secondary` | 默认草稿态 |

**反例**：不要为 `selected` 候选另起新色组——它使用 `ring-2 ring-primary` + 角标 ✓ 表达，底色仍走 completed 组。

### 3.2 字体

`globals.css:1` 已加载 Inter / JetBrains Mono / Space Grotesk；Tailwind alias 已配置。

| alias | family | 使用场景 |
|---|---|---|
| `font-display` | Space Grotesk | 节点标题、品牌词、模式名（Untrusted/On request 等）、空画布提示 |
| `font-sans` | Inter（默认） | 正文、表单 label、对话 bubble、按钮文字 |
| `font-mono` | JetBrains Mono | 文件名（`NEON_REF_01.JPG`）、job ID、时间码、JSON 折叠详情、版本号（`atelier.tools.v1`） |

**字号分级**（Tailwind alias）

| size | px | 用途 |
|---|---|---|
| `text-[10px]` | 10 | 状态徽章、uppercase 元数据、超紧凑标签 |
| `text-[11px]` | 11 | 节点 metadata、tool call 概要 |
| `text-xs` (12) | 12 | 表单 label、按钮文字（紧凑）、chip |
| `text-sm` (14) | 14 | 表单输入、对话气泡、按钮文字（标准） |
| `text-[15px]` / `text-base` (16) | 15–16 | 节点标题、Agent panel header |
| `text-lg` (18) | 18 | 模态框标题（罕用） |

**禁用**：`text-2xl` 及以上仅出现在空画布的"sketchbook"提示中，正文/控件中绝不使用。

### 3.3 间距 / 半径 / 阴影

**间距节奏**：4 / 6 / 8 / 12 / 16 / 24（Tailwind 1/1.5/2/3/4/6）。节点内 padding 默认 16（`p-4`）；面板与浮层之间 gap 12（`gap-3`）；候选 grid 之间 gap 8（`gap-2`）。

**半径**

| token | 像素 | 用途 |
|---|---|---|
| `rounded-md` | 6 | 表单 input/select、内嵌按钮、状态徽章 |
| `rounded-lg` | 8 | 节点容器、候选卡 |
| `rounded-xl` | 12 | 浮层 inspector |
| `rounded-2xl` | 16 | toolbar capsule、agent panel、sequence strip 外框 |
| `rounded-full` | ∞ | 状态徽章（pill）、avatar |

**阴影**：浮层基线 `shadow-2xl shadow-black/40`（已用于节点：`AtelierShell.tsx:415`）；toolbar/agent panel 不主动加阴影，依赖玻璃边线。

**反例**：不要使用 Tailwind 默认 `shadow` / `shadow-md` / `shadow-lg` ——它们对暗背景几乎不可见，浪费 paint。要么用 `shadow-2xl shadow-black/40`，要么不加。

### 3.4 动画

| 用途 | 时长 | easing |
|---|---|---|
| 状态切换（badge 变色、节点边框变色） | `transition` 默认 150ms | `ease` |
| 浮层进入/退出（inspector、approval card） | 200–250ms | `ease-out`（Framer Motion `spring` 备用） |
| 按钮 press | 即时 | `active:scale-95`（已存于 `glass-button` 工具类） |
| Tooltip 显示 | 150ms + 250ms delay | `ease`（已存于 `.btn-tip:hover::after`） |
| 候选 thumbnail 渐入 | 300ms | `ease-out` + `opacity 0→1` |
| Agent thinking pulse | 1500ms infinite | `ease-in-out`，仅 dot 缩放 ±10%，不亮度变 |

**禁用**：旋转 logo 装饰、parallax、hover 浮起 1px 这种 ambient motion——Atelier 是创作环境，不是营销页。

### 3.5 Tooltip 模式

直接用 `.btn-tip` + `data-tip="..."` 属性（`globals.css:78-104`）：

```html
<button class="btn-tip glass-button" data-tip="Fit view (F)">…</button>
```

**强制规则**：所有 icon-only button 必须配 `data-tip`，文字按钮可不加。`data-tip` 内容应包含动作 + 快捷键（如有）。

---

## 4. Information Architecture（v0.3 重构）

```
┌─────────────────────────────────────────────────────────────────────┐
│ Toolbar Capsule (top-left)              [Selection Action Bar]      │
│ [Vid][Img][Idea] [Ask Agent]            ↑ floats over selected node │
│                                                                     │
│                                                  ┌── Right Rail ──┐ │
│                                                  │ Agent (only)   │ │
│                                                  │ ╭────────────╮ │ │
│                                                  │ │ message    │ │ │
│   Main Canvas (infinite, dotted)                 │ ╰────────────╯ │ │
│                                                  │ [approval]     │ │
│   [media] [media]──[media]                       │ [composer]     │ │
│      ╲      ╲                                    │ Preview Execute│ │
│      [draft] [media] [media]                     └────────────────┘ │
│                                                                     │
│             ┌─ Composer (floating) ─────────────┐                   │
│             │ T2I I2I T2V I2V R2V V2V Audio     │  ← appears on     │
│             │ [refs] prompt textarea            │    select draft / │
│             │ [model][16:9][5s][⚙][1×] [↑]    │    "+ Generate"   │
│             └───────────────────────────────────┘                   │
│                                                                     │
│ ┌─ Sequence Strip ────────────────────────────────────────────────┐ │
│ │ [Clip A] [Clip B] [+]                       TOTAL 00:15:00      │ │
│ └─────────────────────────────────────────────────────────────────┘ │
│ [Minimap][Fit][⌕−][slider][⌕+]                                      │
└─────────────────────────────────────────────────────────────────────┘
```

| Zone | 位置 | 默认尺寸 | 折叠/隐藏态 | 备注 |
|---|---|---|---|---|
| Toolbar Capsule (创建+Agent) | top-left, abs | 高 44, 内距 6 | — | glass-panel rounded-full，**比 v0.2 更窄** |
| Main Canvas | 主区填充 | 自适应 | — | dotted grid，纯黑底，**主战场** |
| Selection Action Bar | 浮在选中节点上方 ~40px | 高 32, w 自适应 | 选中时出现，否则不存在 | 仅 icon + 数字徽章；rounded-full + glass |
| Composer | 浮在选中 Draft 下方 / 画布中央 | 520w × 自适应 | 仅生成场景出现 | 单一生成入口，**取代 v0.2 Inspector** |
| Right Rail (Agent only) | right, abs | 380w × full-h | 56w handle | **删除 v0.2 Node tab**；纯 Agent |
| Sequence Strip | bottom, abs（不全宽，让出 minimap） | 96h × `inset-x-4 right-[396px]` | 28h handle | 比 v0.2 矮 36px |
| Minimap + Zoom rail | bottom-left, abs | 高 32 | — | 从 v0.2 顶部 zoom 控件迁过来，与 LibTV/RHTV 一致 |

**v0.3 决策原则**：
- **节点不是配置容器**——节点是结果（媒体）或意图（draft 文本）。配置从节点中**全部抽离**，进入 Composer 浮窗。
- **单一 Composer**——所有生成配置（model / prompt / refs / params / 提交）都走它。Composer 的 dropdown chip 行实现"多层压缩"：默认看到的就是一行 chip，需要时才点开 dropdown。
- **Selection Action Bar 是节点的"操作行"**——播放、分支、加 sequence、删除等动作全在这里，不再嵌进节点卡。
- **Right Rail 只剩 Agent**——v0.2 的 Node tab 被 Minimap 取代（和 LibTV/RHTV 看齐）。
- **Toolbar capsule 收窄**：v0.2 把 zoom 也塞进顶部 capsule；v0.3 把 zoom 移到 bottom-left 与 minimap 同区，顶部 capsule 只剩"创建 + Ask Agent"。

### 4.1 反例

- ❌ 节点卡内嵌 prompt textarea / model dropdown / Generate CTA（v0.1 + v0.2 错了两版）
- ❌ 持续可见的"右栏 properties tab"——LibTV / RHTV 都没有，因为它和 Agent 抢右侧空间
- ❌ Toolbar capsule 横通画布顶——空间浪费且与 Agent 头部抢视觉
- ❌ Composer 嵌进 Right Rail 底部——Composer 应该和 canvas content 同层，不进 chrome

---

## 5. Component Patterns（v0.3 重构）

> v0.3 新增 / 重构组件：**Composer**、**Selection Action Bar**、**Minimap rail**。删除：v0.2 的 Floating Inspector（被 Composer 取代）。

### 5.1 Toolbar Capsule（top-left · 收窄版）

**用途**：创建节点 + 视图导航。

**结构**（v0.3 简化为两段）：

```
[Video][Image][Idea] | [Undo][Redo] | [Ask Agent]
```

- **创建组**：Video / Image / Idea —— 默认创建在视口中心；Image 触发文件选择器
- **历史组**：Undo / Redo（v1 可暂时灰态 + tooltip "Coming soon"）
- **Agent 触发**：Ask Agent（`Sparkles` icon），点击聚焦 Right Rail composer

**zoom / fit / minimap 已迁出**到 §5.8 Bottom Navigation Rail。这样顶部 capsule 真正只剩"我要创建什么"+"我要叫 Agent"。

**className 范本**（基于现有 `.glass-button` 与 `bg-primary`）：

```tsx
<div className="absolute left-4 top-4 flex items-center gap-1 rounded-2xl border border-glass-border bg-glass backdrop-blur-md p-1">
  <button className="btn-tip flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 text-xs font-semibold text-white" data-tip="New Video Node (V)">
    <Film size={14}/> Video
  </button>
  <button className="btn-tip rounded-md p-1.5 text-text-secondary hover:bg-hover-bg" data-tip="New Image Node (I)">
    <ImageIcon size={14}/>
  </button>
  …
</div>
```

**反例**：
- 不要把每个按钮都做成主色填充（Variant 基线只有 Video 是主色，其他是 ghost）
- 不要让 capsule 横通 100% width
- 不要在 capsule 外另起一排创建按钮（重复入口）

### 5.2 Composer（v0.3 新核心组件 · 取代 v0.2 Floating Inspector）

**用途**：所有"生成"配置的唯一入口。无论是用户手动 + Generate、还是选中 DraftNode 提交、还是选中 MediaNode 重新生成 / branch，都用同一个 Composer。

**设计灵感**：LibTV 居中浮窗 + RHTV 选中节点下方浮窗。两者共同点是 **dropdown chip 行** 实现"多层压缩"。

**触发场景**：
| 来源 | Composer 出现位置 |
|---|---|
| Toolbar `+ Video` 点击（无选区） | 视口中央 |
| 选中 DraftNode | 浮在 Draft 下方 16px |
| 选中 MediaNode 后 Action Bar 点 ⚙ Re-generate | 浮在 Media 下方 16px |
| Agent 在 approval 时点 "Edit prompt" | 视口中央，pre-fill |

**默认尺寸**：`520 × auto`（无候选 ~220h；含 reference thumbs ~280h）。**永远不超过 600h**——超了说明 dropdown 没起作用。

**结构**：

```
┌─ Composer ────────────────────────────────────────┐
│ [T2I] [I2I] [T2V] [I2V] [R2V] [V2V] [Audio] [×]  │ ← 操作类型 tab + 关闭
├───────────────────────────────────────────────────┤
│ [ref_thumb][ref_thumb][+]                         │ ← 引用条（可拖入）
│ ┌─────────────────────────────────────────────┐  │
│ │ Describe what you want to generate. Use @   │  │ ← prompt textarea
│ │ to mention canvas nodes.                    │  │
│ └─────────────────────────────────────────────┘  │
│                                                   │
│ [Wan 2.7 ▾] [16:9·720p ▾] [5s ▾] [⚙] [4× ▾] [↑] │ ← chip 行（核心）
└───────────────────────────────────────────────────┘
```

**chip 行（核心多层压缩机制）**：
- 每个 chip 是 `glass-input` rounded-md 6px padding，含值 + chevron-down icon
- 点击展开 dropdown popover，**popover 收起后画布回到无 config 占据状态**
- chip 顺序固定：`Model · Aspect/Resolution · Duration · Advanced gear · Count · Submit`
- Submit 是 primary 圆形按钮 `bg-primary text-white rounded-full p-1.5`，含 `↑` icon
- chip 行高度永远是 `~36px`，无论 dropdown 状态

**Tab 行（操作类型）**：
- 7 个 tab：T2I / I2I / T2V / I2V / R2V / V2V / Audio
- 选中态 `bg-primary/15 text-primary`，未选 `text-text-muted`
- tab 切换会动态调整 reference 区（T2I 不要 ref，I2V 必须有 1 个，R2V 可多个）

**Capability mismatch**：当用户挂的 reference 类型与 model 不匹配，Composer **顶部插入 amber inline error**（不在节点上插），Submit 按钮置灰。

**className 范本**：

```tsx
<div className="absolute z-40 w-[520px] rounded-xl border border-glass-border bg-elevated/95 shadow-2xl shadow-black/40 backdrop-blur-xl">
  <div className="flex items-center gap-1 border-b border-border-subtle px-2 py-1.5">…tabs…</div>
  <div className="p-3 space-y-2">
    <div className="flex items-center gap-1.5">…ref thumbs…</div>
    <textarea className="w-full resize-none rounded-md bg-transparent px-2 py-1.5 text-[13px]" />
    <div className="flex items-center gap-1.5 pt-1">…chip 行…</div>
  </div>
</div>
```

**反例**：
- ❌ Composer 内部展开 model 卡 / params 表格——所有非默认值的设置都收进 dropdown
- ❌ Composer 占据画布超过 60% 高度
- ❌ "Advanced" 手风琴常驻展开
- ❌ 让 Composer 成为 persistent panel——它是 **召之即来挥之即去** 的浮层

### 5.2-bis Selection Action Bar（v0.3 新组件）

**用途**：选中一个 / 多个节点时，**浮在选中区域上方** ~12px 的小工具栏，承载该类型节点的快速操作。灵感：Figma / LibTV 顶部浮动栏 / RHTV 节点上的 download · fullscreen 小条。

**默认尺寸**：高 32, 内距 4。宽度自适应（按 icon 数量）。

**位置算法**：
- 单选：节点 bounding box 顶部居中，y = node.top - 40
- 多选：选区 bounding box 顶部居中
- 节点贴近视口顶部时翻到节点底部
- 节点拖动时跟随

**按节点类型差异化**：

| 节点类型 | Action Bar 内容 |
|---|---|
| MediaNode (image) | 预览 / Use as reference / Branch / + Sequence (仅 video) / Delete |
| MediaNode (video) | Play / Use as reference / Re-generate (open Composer) / Branch / Select as take / + Sequence / Delete |
| MediaNode (audio) | Play / + Sequence / Delete |
| DraftNode | Edit (open Composer) / Approve & generate / Duplicate / Delete |
| IdeaNode | Edit / Expand with Agent / Convert to Draft / Delete |
| 多选 (媒体) | Compare side-by-side / Group / Add all to sequence / Delete all |

**className 范本**：

```tsx
<div className="absolute z-40 inline-flex items-center gap-0.5 rounded-full border border-glass-border bg-elevated/95 px-1 py-1 backdrop-blur-md shadow-2xl shadow-black/40">
  <button className="btn-tip rounded-full p-1.5 text-text-secondary hover:bg-hover-bg hover:text-foreground" data-tip="Play">
    <Icon name="play" size={13}/>
  </button>
  …更多 icon…
  <span className="mx-0.5 h-4 w-px bg-glass-border"/>
  <button className="btn-tip rounded-full p-1.5 text-text-secondary hover:bg-red-400/20 hover:text-red-200" data-tip="Delete">
    <Icon name="trash" size={13}/>
  </button>
</div>
```

**反例**：
- ❌ Action Bar 内放 dropdown / select / textarea 这种重型控件——重型走 Composer
- ❌ 持续可见 / 不消失——必须 selection-driven
- ❌ 太多按钮（> 7）——超过 7 个就该问"哪些能合并到 ⚙ overflow menu"

### 5.2-tris (legacy reference) Floating Inspector for text nodes only

v0.3 几乎不用浮窗 inspector。**唯一例外**：IdeaNode / PlanNode 的 "rename + edit"，仍用极简 popover（200w，只有 title + textarea），其他都走 Composer 或 inline edit。

**为什么 Floating 而不是 Right Rail tab**：Right Rail 永远是 Agent，Inspector 浮在画布上贴近节点。这样 (1) 编辑节点时 Agent 面板不消失（PRD §14 要求 always available），(2) 节点上下文空间联想保留，(3) 不引入第四块固定栏。

**触发**：`selectedNodeId !== null`
**位置算法**：默认 `node.x + node.width + 16, node.y`；右边超画布或被 Right Rail 遮挡（视口右侧 416px 内）→ 翻转到 `node.x - 340 - 16`；若窗口高度不足，inspector 顶部对齐节点顶部，内部 `overflow-y-auto`，最大高 `min(80vh, 640)`。

**默认尺寸**：`340 × auto`，max-height `min(80vh, 640px)`，内部 scroll。

**结构**：
- Header：节点 type icon + 节点 title（可点击重命名）+ 状态徽章 + 关闭 `X`
- 主体：节点类型差异化字段（见 §6）；Video Node 主体最长，分为 Config 段 + Candidates 段
- Footer：操作组（Duplicate / Branch / Add to Sequence / Delete with confirm）

**className 范本**：

```tsx
<aside className="absolute z-30 flex w-[340px] flex-col rounded-xl border border-glass-border bg-elevated/95 shadow-2xl shadow-black/40 backdrop-blur-xl"
       style={{ maxHeight: "min(80vh, 640px)" }}>
  <header className="flex items-center justify-between border-b border-border-subtle px-3 py-2.5">…</header>
  <div className="flex-1 space-y-3 overflow-y-auto p-3">…</div>
  <footer className="flex items-center justify-end gap-1 border-t border-border-subtle px-3 py-2">…</footer>
</aside>
```

**反例**：
- 不要让 Inspector 全屏接管画布（那是 Studio 右栏模式）
- 不要把 Inspector 嵌进 Right Rail 的 tab（破坏 Agent 上下文）
- 不要把 Inspector 内的字段重复内嵌到节点卡——节点卡只展示状态，inspector 才是编辑入口

### 5.3 Right Rail（Agent only · v0.3 删除 Node tab）

**用途**：Agent 对话与审批。**v0.3 删除了 v0.2 的 Node tab**——Node 全局导航交给 Minimap（§5.8），单节点编辑/操作交给 Composer（§5.2）+ Selection Action Bar（§5.2-bis）。

**为什么删 Node tab**：LibTV / RHTV 都没有右栏 Node 列表，因为它和 Agent 抢空间，且 Minimap + 画布拖拽已经能完成"找节点 / 跳转"。Atelier 跟随。

**默认宽度**：380w（v0.2 是 400w，收 20px 给 canvas）；折叠到 56w handle。

**结构**：
- Tab header：`Agent | Node` 切换 + 折叠/展开 chevron
- Agent body：见 §7 Agent Panel Deep Spec
- Node body（v1 简版）：节点列表（icon + title + status pill），点击跳转视口并选中

**宽度**：默认 400px；`< 1024px` 折叠到 64px handle；折叠态露出 4 个垂直图标（Agent message badge / Node count badge / Approval badge / Settings）。

### 5.4 Sequence Strip

**用途**：把已选 take 摆成粗剪。

**结构**：
- 左侧 SCRIPT/TITLE 区（label `font-mono uppercase text-[10px]`）
- 横滚轨道：clip 卡片（含 thumbnail/title/duration bar），尾部一个 `+ add slot`
- 右侧 `TOTAL 00:15:00`

**clip 卡片**：
- 高度 84，宽度 160（4 个时铺满 800w；超过 4 个滚动）
- 顶部 1px 进度条用 emerald（已选 completed），bottom 显示 title `text-xs`
- 拖拽手柄 = 整张卡片，rect drop indicator 用 primary 色

**反例**：不要在 v1 加多轨（音轨/特效轨）；不要做 NLE 时间刻度尺。

### 5.8 Bottom Navigation Rail（v0.3 新增）

**用途**：与 LibTV / RHTV 一致，把"画布导航"工具集中到 bottom-left，与"创建工具"（top-left toolbar capsule）分离。

**结构**：

```
[Minimap toggle] | [Fit] [⌕−] [— slider —] [⌕+] [100%]
```

- **Minimap toggle**：切换右下角的 minimap 浮窗（默认隐藏）
- **Fit**：缩放到所有节点 bounding box
- **⌕− slider ⌕+**：滑动 zoom，0.25–3 范围；当前值实时显示
- 全部 icon-only，配 `data-tip`

**样式**：单条 glass-panel rounded-full，高 32, 内距 6, `bottom-4 left-4`。

**Minimap 浮窗**（toggle on 时）：
- 位于 `bottom-12 left-4`，宽 200, 高 132
- 节点用 1px primary dot 表达（status 不区分），视口框是 `border-2 border-primary`
- 点击 minimap 跳转视口

### 5.5 Approval Card（in Agent Panel）

**用途**：呈现"Agent wants to do X"，用户决断 Approve / Reject。

**结构**：
- 标识带：`ACTION REQUIRED`（amber，`font-mono uppercase text-[10px]`） + 摘要图标
- 标题行：natural-language summary（"Agent wants to create a new **Video Node**: 'Rainy Rooftop Chase'."）
- 影响范围 chip 行（可选）：`[Creates 1 node][No spend][Reversible]` —— 标识可逆性与是否扣费
- 双 CTA：`Approve` (primary fill) + `Reject` (ghost) 等宽
- 折叠详情：`plan_trace.json` 折叠条（mono、点开看原始）

**className 范本**（基于 amber 状态色组）：

```tsx
<div className="rounded-xl border border-amber-300/40 bg-amber-400/[0.06] p-3">
  <div className="mb-2 flex items-center gap-1.5">
    <ShieldCheck size={13} className="text-amber-200"/>
    <span className="font-mono text-[10px] uppercase tracking-wide text-amber-200/90">Action required</span>
  </div>
  <p className="text-sm text-foreground">…</p>
  <div className="mt-3 grid grid-cols-2 gap-2">
    <button className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-white">Approve</button>
    <button className="rounded-md border border-glass-border px-3 py-2 text-sm text-text-secondary hover:bg-hover-bg">Reject</button>
  </div>
</div>
```

**反例**：
- 不要用 red 表示 approval_required（red 留给 failed / denied）
- 不要把 raw JSON 直接铺在卡内——必须折叠

### 5.6 Status Badge / Chip

**Status Badge**（节点 / 候选 头部右上）：

```tsx
<span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase ${statusTone(status)}`}>{status}</span>
```

**Chip**（节点头部小标识、capability 标签、edge meaning）：

```tsx
<span className="rounded border border-glass-border bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase text-text-muted">…</span>
```

### 5.7 Connection Line

**用途**：reference→video / video→branch 的视觉链接。

| 状态 | 线型 | 描边 |
|---|---|---|
| Proposed / draft | dashed `stroke-dasharray="6 4"` | `stroke-primary/40` |
| Established | solid | `stroke-primary/60` |
| Hovered | solid | `stroke-primary` + `drop-shadow(0 0 6 currentColor)` |
| Selected (edge) | solid | `stroke-accent/70` |

控制点：bezier，端点切线水平方向（`x1 y1 → x1+dx, y1; x2-dx, y2 → x2 y2`，dx 取 `Math.max(40, |x2-x1|*0.3)`）。

边上中点可选放 chip 显示 edge meaning（`derived_from / uses_reference / generates / belongs_to_plan`），默认隐藏，hover 时浮现。

---

## 6. Node Specifications（v0.3 重构 · "Media-as-node"）

> **v0.3 总原则**：节点尽量是它**承载的内容本身**——媒体节点 = 媒体本体；文字节点 = 文字气泡；不再有"装内容的卡"。配置归 Composer，操作归 Action Bar。
>
> 节点类型从 v0.2 的 "1 张大卡 + Inspector" 收敛为 5 类轻量原语：
> 1. **MediaNode**（image/video/audio）—— 媒体本体
> 2. **DraftNode**（intent / 待生成的"想法"）—— 超薄文字卡
> 3. **IdeaNode**（自由文本 seed）—— 文字气泡
> 4. **PlanNode**（Agent 创建的 plan）—— 文字气泡 + bullet list
> 5. **ReferenceNode** = ImageMediaNode 的特例（用作 reference 时显示 link chip）

### 6.1 Common Node Shell（v0.3 弱化）

```tsx
<div
  className={`absolute rounded-lg border bg-elevated/92 shadow-2xl shadow-black/40 backdrop-blur-xl ${
    isSelected ? "border-primary/70 ring-2 ring-primary/20" : "border-glass-border"
  }`}
  style={{ transform: `translate(${x}px, ${y}px)`, width, minHeight: height }}
>
  {/* Header: drag handle, type icon, title, status badge */}
  {/* Body: type-specific */}
</div>
```

来源：`AtelierShell.tsx:413-444`（v0.2 形态，v0.3 仍复用 selected/hover 视觉，但节点本体不再有 header bar / chip row）。

**v0.3 节点的"chrome"原则**：
- 默认状态下，节点 = 内容本身（图、视频、文字）；**没有边框 / 没有 header / 没有 status pill**
- **hover** 时浮出 type label chip（左上，~20h）+ 状态 dot（右上，6×6 圆）
- **selected** 时加 `ring-2 ring-primary` + Selection Action Bar 浮在上方（§5.2-bis）
- **status 视觉**仅用 ring 颜色表达：completed = primary（选中态本身就是它），processing = blue ring 1px + spinner 角标，failed = red ring 1px + alert 角标

**反例**：
- ❌ 给媒体节点加 header / footer / chip 行（v0.2 错了）
- ❌ 节点未选中也显示边框 / 按钮——zoom out 看 50 个节点时所有 chrome 应该消失，画布回到"图墙"
- ❌ 用强色（紫/绿/红）做默认描边——状态色只在状态非 normal 时出现

### 6.2 IdeaNode（文字气泡 · 自由 seed）

**默认尺寸**：`220 × min-72`，最大 `260 × 200`，超过 200h 截断 + ellipsis
**用途**：自由文本 seed、场景注释、prompt 片段

**视觉**：
- 无 header bar；左上角 hover 时浮出 `Lightbulb` chip + "IDEA" 标签
- 边框：极淡 `border-amber-300/20`（让它"读起来像便签"，不是装内容的容器）
- 背景：`bg-amber-400/[0.04]` 暖色微染
- 内容：直接是文字 `text-[13px] text-foreground/90 leading-relaxed`
- 选中：`ring-2 ring-primary` + Action Bar 浮上方

**操作**（在 Action Bar 里，不嵌节点）：Edit · Expand with Agent · Convert to Draft · Delete

**反例**：
- ❌ Idea 内放 model / 生成按钮（v0.2 没错过，但 v0.3 强调一遍）
- ❌ 给 Idea 加完整 header（type icon + title + status pill）——v0.3 IdeaNode 没有 title 字段，title 就是文字内容的第一行

### 6.3 ImageMediaNode（图片本体）

**默认尺寸**：`180 × 180`（square 默认）；如媒体本身是横构图则 `200 × 120`，竖构图 `120 × 180`。**最大不超过 240w**——v0.2 的 280w 也已经过宽。
**用途**：上传或生成的图（可作为 reference）

**结构**：
- 节点 = 图本身（`<img>`），`object-cover` + `rounded-md`
- **未 hover 未选中**：纯图，无任何 chrome。zoom out 时画布看起来就是"图墙"
- **hover**：左上 type chip "📷 IMG" + 文件名 chip 浮在右下 `bg-black/55 px-1.5 py-0.5 text-[10px] font-mono text-white/80`；hover 浮出 link chip `2 used` 表明被引用次数
- **selected**：`ring-2 ring-primary` + Action Bar 上方
- **作为 reference 被连出**：右中输出 handle 显示为 4×4 圆点（仅在 hover 时浮现）

**className 范本**：

```tsx
<div className={`absolute group overflow-hidden rounded-md ${
  isSelected ? "ring-2 ring-primary" : ""
}`} style={{ width, height, transform: `translate(${x}px, ${y}px)` }}>
  <img src={src} className="h-full w-full object-cover" />
  {/* hover-only chrome */}
  <span className="absolute left-1 top-1 hidden rounded bg-black/65 px-1.5 py-0.5 text-[10px] font-mono uppercase text-white/85 group-hover:block">📷 img</span>
  <span className="absolute right-1 bottom-1 hidden rounded bg-black/65 px-1.5 py-0.5 text-[10px] font-mono text-white/80 group-hover:block">{filename}</span>
</div>
```

**反例**：
- ❌ image node 加 header / footer 占据卡内空间
- ❌ image node 默认显示文件名 / link 计数——zoom out 看 30 张图时这些都是噪音

### 6.3-bis VideoMediaNode（视频本体）

**默认尺寸**：`200 × 113`（16:9）。**最大不超过 240w**。
**用途**：generation 结果 / 用户上传的视频

**结构**：
- 节点 = 视频 poster（`<img>` 用首帧 / 提供方 thumbnail）；`<video>` 仅在 hover/selected 时挂载（节省内存——满画布 50 个视频不能都 mount）
- **未 hover**：纯 poster + 中央很小的 play 圆点（`14×14 rounded-full bg-black/50` + play icon size 8）
- **hover**：左上 chip "▶ VIDEO"；右下 duration chip `0:05`；中央 play 按钮放大到 `32×32`
- **selected**：ring + Action Bar；play 按钮变 primary 色
- **selected-as-take**（被选作 sequence 的 take）：`ring-2 ring-primary` + 左下 `[✓ Selected]` 永久 chip
- **processing**：覆盖蓝色 overlay + 中央 spinner + "47%" 文本
- **failed**：覆盖红色 overlay + alert icon + 极短 error 文本

**className 范本同 ImageMediaNode**，仅媒体元素差异。

### 6.3-tris AudioMediaNode（音频）

**默认尺寸**：`200 × 56`
**用途**：narration / SFX / music 节点

**结构**：
- 内容 = 静态波形 PNG / SVG（用提供方生成的 thumbnail；没有就生成一个简单的 32-bar 灰色波形）
- 中央 play 按钮 `16×16`
- 左上 hover chip "🔊 AUD"
- 右上 hover chip duration `0:30`

### 6.4 DraftNode（v0.3 取代 Video Generation Node · 超薄"意图卡"）

**默认尺寸**：`240 × ~80`（无 ref）；`240 × ~110`（含 ref 缩略行）。**最大 260w / 140h**。
**用途**：承载"我打算生成什么"的意图——这是 Atelier 区别于 LibTV/RHTV 的差异化所在（PRD §10.7 "3 drafts side by side"）。

**为什么需要它**（LibTV/RHTV 都没有）：
- LibTV/RHTV 的 Composer → 直接生图，没有"intent" 中间态
- Atelier Agent 的核心能力是"提议 3 个方向供评审"——这个评审环节需要 3 个并排可见的对象，而不是 Composer 反复调出
- 所以 DraftNode 是 **Composer 的 persistent 化** —— Agent 可以一次创建 3 个 Draft，用户拉看每个，approve 时 inline 提交

**结构**（极薄）：

```
┌────────────────────────────────────┐
│ ✦ Cinematic interpretation         │ ← intent label (第一行)
│ Wan 2.7 · 1280×720 · 4 takes       │ ← config 摘要
│ [refs thumb 24×24 ×N]              │ ← reference 缩略（可选）
└────────────────────────────────────┘
```

**视觉**：
- 边框：`border-glass-border` 默认；approval pending → amber ring
- 背景：`bg-elevated/85`
- 第一行：display font, `text-[13px] text-foreground`，含 ✦ icon (primary)
- 第二行：`text-[11px] text-text-secondary` 摘要（model · resolution · count）
- ref 缩略：4 张 `24×24 rounded` 横排
- **节点不放 textarea / Generate button**——所有这些点击节点 → 浮出 Composer

**状态视觉**：
- `draft`（待 approve）：边 `border-amber-300/40`，左下小 amber dot 提示"待你过目"
- `approved`（已批准但未跑）：`border-primary/40`
- `running`（已提交）：`border-blue-400/50` + 第二行右侧加 spinner
- `completed`（产生了 takes）：边变淡 `border-glass-border`；通常此时 user 已经在看 take，draft 退到次要——可考虑 `opacity-70`

**操作**（在 Action Bar）：Edit (open Composer prefilled) · Approve & Generate · Duplicate · Delete

**反例**：
- ❌ Draft 卡内放 prompt textarea 全文——只显示 intent label，全文进 Composer
- ❌ Draft 显示候选 grid——候选是 MediaNode，散布在画布上，不进 Draft
- ❌ Draft 加 Generate CTA 按钮——CTA 在 Composer，Draft 上的"提交"通过 Action Bar 触发

### 6.4-bis 候选生成关系（v0.3 重大变化）

v0.2 把 4 个候选塞在父 VideoNode 的 grid 里。v0.3 改为：

> **候选 = N 个独立 VideoMediaNode，散布在画布上，由父 DraftNode 拉边连出。**

视觉效果：

```
            DraftNode (intent)
              ╱  │  ╲  ╲
       [vid] [vid] [vid] [vid]    ← 4 个独立 MediaNode，水平排列
        ✓
```

- 自动布局：DraftNode 居左中，4 个 MediaNode 在右边 16px 间距、自动 grid 排（2×2 或 1×4，按可用空间）
- 用户可拖动单个 MediaNode 重排
- 选 take = 给某个 MediaNode 加 primary ring + `[✓ Selected]` chip
- branch = 从 take 拉新 DraftNode 出来，承载新 intent

这样：
- 4 个候选是 4 个 ~200×113 的 video tile，比 v0.2 的 480×620 父卡 + 候选 grid 总占 ~480×800 小得多
- 用户可以横向比较 take，因为它们是 **canvas first-class citizens**，不是 grid 单元
- 选 take / branch / 加 sequence 都直接在 MediaNode 上操作，零层嵌套

### 6.5 PlanNode（Agent-created · 文字气泡 + bullet list）

**默认尺寸**：`260 × ~140`，最大 `300 × 220`
**用途**：Agent 创建的 plan 节点，承载"我打算分几步做什么"的概览

**结构**：
- 头部 hover 浮 chip "✦ PLAN · by Agent"
- Body：title 行（`text-[13px] font-semibold`）+ 3-5 条 bullet list（`text-[12px] text-text-secondary`）
- 边框：`border-primary/30`（淡 primary 强调"是 Agent 提议的"）

**关系**：PlanNode 通常向外拉边连出 N 个 DraftNode（"belongs_to_plan" edge），DraftNode 各自再连出 MediaNode 候选。

### 6.6 Selected Take 视觉总结（v0.3 简化）

selected take 必须在**两处**同步呈现：

1. **被选中的 VideoMediaNode**：`ring-2 ring-primary` + 左下永久 `[✓ Selected]` chip（不依赖 hover）
2. **Sequence Strip**：clip 顶部进度条 emerald（如果该 take 已被加入 sequence）

v0.2 的"4 处同步规则"在 v0.3 不适用——v0.3 没有"父节点 hero area"、没有"父节点 mini strip"、没有"inspector candidate grid"，所以候选选中只有"媒体节点本身"和"sequence"两处。

---


## 7. Agent Panel Deep Spec

### 7.1 总体结构

```
┌─ Header ────────────────────────────────────┐
│ [Bot avatar+pulse] Creative Agent · Active  │
│ [permission segmented]  [collapse]          │
├─ Conversation (scroll) ─────────────────────┤
│ U: …                                        │
│ A: …                                        │
│ ┌ Action summary ─────────────────────┐    │
│ │ ✦ Created 3 video drafts            │    │
│ └─────────────────────────────────────┘    │
│ ┌ Plan trace (collapsed mono) ▾────────┐   │
│ └──────────────────────────────────────┘   │
│ ┌ APPROVAL CARD ─ amber ──────────────┐    │
│ │ Agent wants to create Video Node:…  │    │
│ │ [Approve]  [Reject]                 │    │
│ └─────────────────────────────────────┘    │
├─ Tool Activity (compact) ────────────────────┤
│ ⚹ canvas.createVideoNode  · running         │
│ ✓ canvas.attachReferenceNode · 23s          │
├─ Composer ──────────────────────────────────┤
│ Ask Agent…                                  │
│ [📎][🎤]                  [Preview][Execute]│
└─────────────────────────────────────────────┘
```

### 7.2 Header

- Avatar：`Bot` icon 在 `bg-primary/20 text-primary rounded-md` 内；右下角脉冲点 `bg-emerald-400` 仅在 turn `pending` / `waiting_approval` / `running` 时跳动
- 名称：`Creative Agent`（display font）+ status `· Active / · Thinking… / · Awaiting approval / · Idle`（text-secondary）
- 权限选择器：见 §7.5
- Collapse chevron：`>>` 折叠到 64w

### 7.3 Conversation

- User bubble：右对齐，`max-w-[85%]`，`bg-primary/15 text-foreground rounded-2xl px-3 py-2`
- Agent bubble：左对齐，`bg-glass text-foreground rounded-2xl px-3 py-2`，前缀 `Bot` icon
- 消息时间戳：`text-[10px] text-text-muted`，hover 时浮现
- 自动连续消息（同一发送者 < 1 min）：合并 bubble，时间戳只在最后一条显示

### 7.4 Action Summary 卡

- 用途：用人类语言概括 Agent 一次完成的动作（区别于 Approval Card 的"待执行"）
- 视觉：`rounded-md border border-border-subtle bg-glass px-3 py-2`
- 前缀图标：`Sparkles` size 12 in primary
- 文本示例：`Created 3 draft video nodes from your seed.`
- 跟随：折叠"plan trace"详情（点击展开 mono JSON）

### 7.5 Permission Segmented Control

四种模式（来自 `AtelierApprovalMode`）：

```
[Untrusted | On failure | On request | Never]
```

**视觉**：
- 容器：`inline-flex rounded-full border border-glass-border bg-glass p-0.5`
- 选中项：`bg-primary text-white px-2.5 py-1 rounded-full text-[11px] font-semibold`
- 未选项：`px-2.5 py-1 text-[11px] text-text-muted hover:text-foreground`
- 当前选中应附加底部 hint（`text-[10px] text-text-muted`），描述该模式语义（同 PRD §14.2）

**反例**：不要用 "Cautious / Balanced / Autopilot / Unrestricted" 这套（roadmap 里的别名）—— 后端字面量是 `untrusted/on_failure/on_request/never`，**视觉文案必须严格对齐**字面量翻译，避免用户翻译断层。

### 7.6 Composer

- 输入：`<textarea>` 自适应高度，最小 2 行，最大 6 行，超出滚动
- 占位符：上下文相关——空选区 `Ask Agent…`；选中节点 `Ask Agent about "Rainy Rooftop"`；选中边 `Ask Agent about this connection`
- 附件：`Paperclip` icon button（v1 仅打开文件选择，预留接口；不可点出 unimplemented modal）
- 语音：`Mic` icon button（v1 灰态 + tooltip "Coming soon"）
- 双 CTA：
  - **Preview**（白底/ghost）：调 `/agent/plan` 返回 plan，不执行；UI 在对话流中插入"Plan preview"卡，列出 tool calls 概要
  - **Execute**（white fill / 主色 fill 二选一，与 §5.5 Approve 区分）：调 `/agent/turns` 真实执行；按 permission mode 分流

**视觉差**：
- Preview 按钮：`bg-glass border-glass-border text-foreground`
- Execute 按钮：`bg-foreground text-background`（白填黑字，足够强但不抢主色 primary）—— 与 §5.5 Approve 的 `bg-primary` 区分，避免 user 误以为"Execute = Approve all"

### 7.7 Tool Activity Timeline

**用途**：把 tool call 转成"已发生"的事件流，区别于待批的 Approval Card。

**结构**（每条 1 行）：
```
[● 状态点] [tool 友好名] · [duration / status]    [详情 ▾]
```

- 状态点颜色：proposed=text-muted, running=blue, completed=emerald, denied=text-muted with strikethrough, failed=red
- tool 友好名：把 `canvas.createVideoNode` → "Create video node"；用 mapping 表，**不展示原始命名空间**作为主信息（tooltip 里可显示）
- 详情折叠：参数 + 时长 + result_snapshot 概要（mono `text-[10px]`）

**反例**：不要把 5 个 tool call 挤成长段 mono JSON——用一行/条，详情折叠。

---

## 8. Canvas Interaction

### 8.1 Pan / Zoom / Fit

- Pan：左键空白处拖拽 / Space + 拖拽 / 中键拖拽
- Zoom：滚轮以光标为中心缩放，范围 `0.25 – 3`，Tailwind transform 容器
- Fit View：`F` 键 / Toolbar Fit 按钮，计算所有节点 bounding box，留 64px padding
- 当前 zoom 显示在 toolbar 中部（`text-[11px] font-mono`）

### 8.2 Select / Drag

- Click 节点空白处选中；click 画布空白清除
- Shift+Click 多选（v1 可后置）
- 节点内交互控件（按钮、textarea、select）必须 `e.stopPropagation()` 避免触发拖拽

### 8.3 Connect Drag

- 拖拽节点 handle 圆点，路径用 dashed bezier（连线视觉见 §5.7）
- 释放在合法目标上：变为 solid + 持久化
- 释放在画布空白：弹"create node here"快捷菜单（v1 可后置）

### 8.4 Branch Handle

- 在 Candidate Card hover 时，右下显示 `↗ Branch` 小按钮
- 点击：在画布上以选中候选为 reference，在右侧 320px 处创建新 video node，自动连线

### 8.5 键盘快捷键

| 按键 | 动作 |
|---|---|
| `V` | New Video Node |
| `I` | New Image Node |
| `T` | New Idea/Text Node |
| `F` | Fit View |
| `Space` (hold) | Pan mode |
| `Cmd/Ctrl+Z / Shift+Z` | Undo / Redo |
| `Cmd/Ctrl+D` | Duplicate selected |
| `Delete` | Delete selected (确认) |
| `Esc` | Clear selection / close inspector |
| `/` | Focus Agent composer |

**强制**：所有快捷键在 Toolbar tooltip 中暴露（`data-tip="Fit view (F)"`），不要让快捷键变成只有老用户知道的隐性能力。

---

## 9. Connection Visuals

| 用途 | 线型 | 描边 | 端点 |
|---|---|---|---|
| Reference → Video（已建立） | solid | `stroke-primary/60` | 圆点 4px primary |
| Reference → Video（拖拽中） | dashed `6 4` | `stroke-primary/40` | 起点圆，终点 ✚ |
| Video Take → Branch | solid | `stroke-primary/60` | 起点 ◇，终点 ▷ |
| Idea → Plan/Video（语义） | dotted `2 4` | `stroke-text-muted/40` | 不含端点 |
| Sequence inclusion（不可见 edge） | — | — | 仅在 Sequence Strip 显示 |

**Edge meaning chip**（hover 显示）：背景 `bg-elevated`, border `border-glass-border`, 圆角 full, 内含 12px 字。来自 PRD/roadmap 的 edge type 字面量：`derived_from / uses_reference / generates / belongs_to_plan / alternative_to / edited_from / added_to_sequence`。

**反例**：不要用彩虹色区分多种 edge type——一旦 edge 类型 ≥ 4，色彩失控。改用线型 + chip 文字。

---

## 10. State Visual Matrix

> 一行覆盖一个状态字面量。颜色严格对齐 §3.1 状态色组。

### 10.1 Node Status (`AtelierNode.status`)

| 字面量 | 边色 | 底色 | 文本 | Icon | 动效 |
|---|---|---|---|---|---|
| `draft` | `border-glass-border` | `bg-elevated/92` | text-secondary | — | — |
| `pending` | `border-blue-400/50` | `bg-blue-400/10` | text-blue-200 | `Loader2` spin | — |
| `processing` | `border-blue-400/50` | `bg-blue-400/10` | text-blue-200 | `Loader2` spin | — |
| `completed` | `border-emerald-400/50` | `bg-emerald-400/10` | text-emerald-200 | `Check` | — |
| `failed` | `border-red-400/50` | `bg-red-400/10` | text-red-200 | `XCircle` | — |

### 10.2 Candidate Status (`AtelierVideoCandidate.status`)

同上 4 态（无 `draft`），叠加 `selected` 视觉（`ring-2 ring-primary` + ✓ chip），不替换底色。

### 10.3 Agent Tool Call (`AtelierAgentToolStatus`)

| 字面量 | 边色 | 文本 | Icon | 备注 |
|---|---|---|---|---|
| `proposed` | `border-glass-border` | text-secondary | dot | 未提交、待审 |
| `approval_required` | `border-amber-300/40` | text-amber-100 | `ShieldCheck` | 触发 Approval Card |
| `completed` | `border-emerald-400/40` | text-emerald-200 | `Check` | timeline 蓝→绿 |
| `denied` | `border-red-400/40` | text-red-200 strike-through | `X` | 用户拒 |
| `failed` | `border-red-400/40` | text-red-200 | `AlertTriangle` | 含 error 文本 |

### 10.4 Agent Turn (`AtelierAgentTurn.status`)

| 字面量 | Header pulse | composer 状态 |
|---|---|---|
| `pending` | text-blue-200 pulse | composer locked, "Thinking…" |
| `waiting_approval` | text-amber-200 pulse | composer locked, Approval Card 高亮 |
| `completed` | text-emerald-200 (1s 后退去) | composer 解锁 |
| `failed` | text-red-200 | composer 解锁，对话流中插入 failure 摘要 |

### 10.5 Permission Mode (`AtelierApprovalMode`)

| 字面量 | UI label | hint |
|---|---|---|
| `untrusted` | Untrusted | Ask before canvas or generation actions. |
| `on_failure` | On failure | Canvas writes may run; generation still asks. |
| `on_request` | On request | Ask only for tools marked as approval-only. |
| `never` | Never | Run allowed tools within hard limits. |

UI label 文案与 `AtelierShell.tsx:113-118` 一致；任何修改必须双向同步。

---

## 11. Empty States & Loading

### 11.1 空画布（无任何节点）

- dotted grid 仍铺满
- 视口正中显示 `font-display text-[15px] text-text-muted`：`Drop a seed. Press V for video, I for image, T for idea.`
- **不要**显示巨型 logo / hero CTA / 渐变 orb。

### 11.2 项目 bootstrap 中

- 画布灰一档 (`opacity-60`)
- toolbar 创建按钮 disabled，附 tooltip "Setting up your atelier…"
- 中央显示 `Loader2` spin + `Loading your atelier…`（≤ 14px）

### 11.3 Agent panel 空对话

- 头部正常显示
- 主体显示 quick prompts 列表（4 条预设建议，rounded chip，点击填入 composer）
  - `Explore 3 directions for…`
  - `Polish my prompt`
  - `Create reference set from…`
  - `Suggest sequence order`

### 11.4 候选 grid 空（节点已 generate 但 0 candidates 完成）

- grid 区显示 4 个 skeleton 卡（`bg-white/[0.03] rounded-md aspect-video animate-pulse`）
- skeleton 数量 = 用户设置的 candidate count

### 11.5 失败重试

- failed 候选卡：媒体区灰底 + `XCircle text-red-300` + error 文本 1–2 行
- 卡内右下显示 `Retry` icon button（`RotateCcw`）
- 整个节点 status 计算：所有候选都失败时 → 节点 `failed`；部分失败 → 节点保持 `processing` 直到全部结算

---

## 12. Anti-Patterns (Do Not)

来自 PRD §16.2 + 本文档实施经验：

1. **不要用紫色品牌渐变作为 Atelier 背景**——保留给品牌时刻（启动画面、share 卡）。
2. **不要把节点放在装饰卡里**——节点本身就是卡，外面再套 card 就是 nested chrome。
3. **不要把 Generate 按钮做成 white fill**——保留给 Execute（agent panel）。Generate 用 `bg-primary`。
4. **不要把 Execute 与节点 Generate 同形同位**——两者点错风险大。Execute 是右下 panel 内、白底；Generate 是节点内、主色填充。
5. **不要在 toolbar / agent panel / sequence strip 之外引入第四块固定栏**——v1 只有这三块 + floating inspector。
6. **不要让 raw JSON 当主信息**——所有 JSON 必须折叠。
7. **不要为 selected 候选只改底色**——必须叠 ring + label，避免色觉障碍误读。
8. **不要让 Tooltip 替代 label**——核心创建按钮第一次出现给用户文字 label，老用户切换到 icon-only 由用户偏好控制。
9. **不要在 Agent 对话流中显示 system prompt**——Atelier 是 creator surface，不是 prompt engineering 工具。
10. **不要把"模式名"翻译得花哨**——permission mode 字面量翻译就是 Untrusted/On failure/On request/Never，不要变 Cautious/Balanced/Autopilot/Unrestricted（roadmap 中的"语义命名"是文档语境，UI 一律字面量）。
11. **不要把节点卡当编辑器（v0.2 教训）**——节点卡是状态展示，不是表单。10+ 节点同屏才是常态；任何让单节点卡 > 320w 的设计都会让画布在中型项目里失效。
12. **不要给媒体节点加 chrome（v0.3 新增）**——image/video/audio 节点 = 媒体本体；未 hover/未 selected 时**只能看到媒体，看不到任何 header/footer/pill/chip**。v0.2 的"compact card with header + chip row + status pill"被 v0.3 取代为"裸媒体 + hover-only chip"。
13. **不要让 Composer 变成 persistent panel（v0.3 新增）**——Composer 是召之即来挥之即去的浮窗，跟随选区出现/消失，不能像 v0.2 Inspector 那样永久占据画布右侧。
14. **不要把候选放进父节点 grid（v0.3 新增）**——候选 = 独立 MediaNode 散布画布，由父 DraftNode 拉边连出。父子嵌套会让父节点必须膨胀（v0.2 错过）。
15. **不要给 Right Rail 加 Node tab（v0.3 新增）**——LibTV / RHTV 都没有，因为 minimap + 画布拖拽已经够。Node tab 只会和 Agent 抢空间。

---

## 13. Accessibility & Responsiveness

### 13.1 可访问性

- focus ring：所有可聚焦元素 `focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:outline-none`
- hit area：所有 icon button 至少 36×36（含 padding）
- 颜色不是唯一信息：状态除了颜色还要附 icon / label
- 键盘可达：所有 Toolbar / Agent / Inspector 操作都有快捷键或 Tab 路径
- ARIA：approval card 用 `role="alertdialog" aria-live="polite"`；agent message stream 用 `aria-live="polite"`

### 13.2 断点

| 断点 | 行为 |
|---|---|
| `≥ 1280px` | 所有区域展开（toolbar 56h, agent panel 400w, sequence strip 132h） |
| `1024–1280px` | agent panel 默认折叠到 64w handle；其余正常 |
| `768–1024px` | agent panel + sequence strip 同时折叠到 handle；toolbar 取消 zoom% 中段 |
| `< 768px` | v1 不支持。显示降级页：`Atelier needs a wider screen. Open it on desktop.` 含 LumenX logo + 一段说明 |

---

## 14. Sign-off Checklist

PR 评审 / 设计验收时勾选：

- [ ] 颜色全部走 `globals.css` CSS var 或 Tailwind alias，无新增十六进制
- [ ] 状态色组与 `AtelierShell.tsx` 现有 `statusTone()` 一致
- [ ] 状态字面量与 `frontend/src/lib/api.ts` union type 完全匹配（包括 `pending` 与 `processing` 都映射到 blue 组）
- [ ] 所有 icon-only button 配 `data-tip`
- [ ] Approval Card 用 amber，不和 failed 的 red 混淆
- [ ] Selected 候选三处视觉同步（卡内 ring + 节点顶部 selected take 区 + sequence strip 进度条）
- [ ] Permission mode UI label 用字面量翻译（Untrusted / On failure / On request / Never）
- [ ] capability mismatch 在 Generate 之前拦截，文案给替代方案
- [ ] 空画布不放 hero
- [ ] Execute 与 Generate 视觉区分明确
- [ ] 键盘快捷键在 tooltip 中暴露
- [ ] 在 ≥1280 / 1024–1280 / 768–1024 / <768 四档断点测试

---

## 15. Live Prototype

本 DESIGN.md 的可视化注解：

- 路径：[`docs/design/prototypes/atelier-phase-bcd.html`](./prototypes/atelier-phase-bcd.html)
- 打开方式：双击或 `open docs/design/prototypes/atelier-phase-bcd.html`
- 切换场景：顶部 segmented control（也支持 URL hash 直达）

| 场景 | hash | 对应 DESIGN.md 节 |
|---|---|---|
| Phase B-1 · Video Node (idle) | `#scene=phase-b-1` | §6.4, §3 |
| Phase B-2 · Video Node (generating + 候选) | `#scene=phase-b-2` | §6.4, §6.5, §10.2 |
| Phase B-3 · Capability Mismatch | `#scene=phase-b-3` | §6.4.1, §15.2(PRD) |
| Phase C-1 · Agent Panel timeline | `#scene=phase-c-1` | §7, §10.4 |
| Phase C-2 · Permission Mode 切换 | `#scene=phase-c-2` | §7.5, §10.5 |
| Phase D-1 · Canvas + 连接线 | `#scene=phase-d-1` | §4, §5.1, §9 |
| Phase D-2 · Sequence Strip 全态 | `#scene=phase-d-2` | §5.4 |

> 原型与本文档冲突时，以本文档为准——原型是设计的"演示快照"，本文档是"约束源"。
