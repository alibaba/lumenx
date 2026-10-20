# LumenX Studio — Cyber Brutalism × Cinematic Restraint Design System v0.2

> Pipeline-first AI 短漫剧创作平台 | "Render Noise into Narrative"

Approval status: pending user review.

Visual direction: **Cyber Brutalism × Cinematic Restraint** — 在原本 dark-first 克制电影感基础上，引入 v0.2 视觉焕新（2026-06-09 commit `e6f86ea`）：
- 主视觉切换为**棱角几何莲花徽标**（白色折线轮廓 + 中央蓝色水晶 + 电路线点缀），替代旧的紫粉渐变方块
- 字标改为**等宽体 LUMENX**（`LUMEN` 白 + `X` 蓝 #646cff 加粗），取代渐变文字
- 新增 banner / scanlines / grid 等"赛博粗野"母题作为可选装饰层
- UI chrome 侧仍保持克制：让生成内容（图像/视频/分镜缩略图）成为画面绝对主角

## Brand Identity v0.2

### Logo Mark
- 资产: `LumenX-cybr.png`（栅格化 1024×1024，frontend/public 与本工作区均有副本）
- 形态: 锐角几何莲花，6 片左右对称花瓣 + 顶部像素化茎杆（5 块方点，3 蓝 2 白）
- 中央: 棱柱蓝色水晶（#646cff）作为视觉重心
- 装饰: 花瓣内部 3 段电路线 + 端点蓝色像素
- 颜色规则: **只用纯白 + 纯黑底 + #646cff**，禁止任何渐变或彩色阴影
- 最小尺寸: 24px（侧栏使用 28-32px）

### Wordmark
```
LUMEN  X         ← JetBrains Mono 14px / weight 700 (LUMEN 白) · weight 900 (X #646cff)
STUDIO           ← JetBrains Mono 9px UPPERCASE letter-spacing 0.2em · 30% white
```
- 不再使用 Space Grotesk 或渐变文本
- `X` 是品牌独立焦点，weight 比 `LUMEN` 高一档

### Slogan
- "Render Noise into Narrative" — Mono 8px UPPERCASE letter-spacing 0.15em / 20% white
- 仅在登录、空态、Splash 等品牌强化时刻出现

### Anti-Patterns (旧主视觉禁用)
- 紫→indigo→pink 渐变 brand-icon 方块（v0.1 旧法）
- 渐变 LumenX 文字（背景 clip text）
- 任何彩色光晕环绕 logo
- 与 logo 同尺寸或更大的装饰文字

## Product Context

- **Product**: LumenX Studio (LumenX 产品家族 — Core + Studio + Atelier + Playground)
- **Audience**: 独立短视频/自媒体创作者 & 专业团队 pre-production
- **Platform**: Next.js 14 + React 18 + Tailwind CSS + Zustand + Framer Motion (桌面 web + pywebview shell)
- **Density**: 中等偏紧凑（创作者同时盯多个 shot），靠层级和留白区分，不靠颜色噪音

## Visual Theme & Atmosphere

### Core Principles
1. **Content is king** — 用户的创作物（图像/视频/分镜缩略图）占视觉权重，UI chrome 低对比保持安静
2. **Cinematic darkness** — 深空黑底 (#050508) 让媒体内容自发光，不需要额外投光
3. **Restrained accents** — neon 蓝/粉仅在 CTA、active state、生成中状态等关键交互节点出现
4. **Brutalist precision** — Logo / 字标 / chrome 用等宽几何 + 强对比，建立"机器制造"的精度感
5. **Purposeful motion** — ease-out-quart 3档时长，每个动画传达意义而非装饰

### Anti-Patterns
- 通用 SaaS admin（表单密集 + 蓝色按钮）
- Figma/Photoshop 多面板专业工具复杂度
- 默认 AI purple-500/indigo-500 模板色
- 多重叠加彩色发光描边（像玩具）
- glassmorphism 滥用导致内容读不清

## Color System

### Seed Layer (--seed-*)
供产品家族共享的品牌种子色，Atelier 未来可在 seed 基础上派生自身语义。

| Token | Value | Role |
|-------|-------|------|
| `--seed-bg` | `#050508` | 深空黑底 |
| `--seed-fg` | `#ededed` | 主文本 |
| `--seed-primary` | `#646cff` | Electric blue — CTA / active |
| `--seed-accent` | `#ff0080` | Hot pink — 警示/品牌时刻 |
| `--seed-surface` | `rgba(255,255,255,0.04)` | 基础面板底色 |
| `--seed-radius` | `8px` | 全局圆角基准 |

### Semantic Layer (--color-*)
产品级角色 token，组件只消费这一层。

| Token | Dark Value | Role |
|-------|-----------|------|
| `--color-bg-base` | `#050508` | 页面底色 |
| `--color-bg-surface` | `rgba(255,255,255,0.04)` | 面板/卡片底色 |
| `--color-bg-elevated` | `#141416` | 浮层/modal |
| `--color-bg-inset` | `rgba(255,255,255,0.02)` | 嵌套区域 |
| `--color-bg-hover` | `rgba(255,255,255,0.06)` | hover 高亮 |
| `--color-text-primary` | `#ededed` | 主文本 |
| `--color-text-secondary` | `#9ca3af` | 二级说明 |
| `--color-text-muted` | `#6b7280` | 辅助/禁用 |
| `--color-border-default` | `rgba(255,255,255,0.08)` | 默认边框 |
| `--color-border-subtle` | `rgba(255,255,255,0.04)` | 微弱分割 |
| `--color-overlay` | `rgba(0,0,0,0.85)` | backdrop |

### Status Tokens (OKLCH)
5 种语义状态，每种携带 -fg / -border / -bg 三元组。

| Status | Hue | Example use |
|--------|-----|-------------|
| pending | 230 (蓝) | 等待生成 |
| processing | 80 (琥珀) | 生成中 |
| completed | 155 (绿) | 完成 |
| failed | 25 (红) | 失败 |
| starred | 90 (金) | 选中/精选 |

## Typography

三字体体系，三档层级：

| Tier | Font | Use | Characteristics |
|------|------|-----|----------------|
| Chrome | JetBrains Mono | Section headers, status badges, metadata | UPPERCASE, letter-spacing 0.18em, 10-11px |
| Body | Inter | Input values, descriptions, inline text | Normal case, 12-13px |
| Display | Space Grotesk | Primary CTAs, focal headings | Semibold, negative tracking, 14-16px |

## Spacing & Layout

8px base grid. 核心 spacing tokens:

| Token | Value | Use |
|-------|-------|-----|
| `--spacing-xs` | 4px | 紧密元素间距 |
| `--spacing-sm` | 8px | 元素内边距、小间隙 |
| `--spacing-md` | 16px | 组件标准 padding |
| `--spacing-lg` | 24px | section 间距 |
| `--spacing-xl` | 40px | 大区域分隔 |

## Radius & Elevation

| Token | Value | Use |
|-------|-------|-----|
| `--radius-sm` | 4px | badge, chip |
| `--radius-md` | 8px | button, input, card |
| `--radius-lg` | 12px | modal, panel |
| `--shadow-subtle` | `0 1px 2px rgba(0,0,0,0.4)` | card 微影 |
| `--shadow-elevated` | `0 8px 32px rgba(0,0,0,0.6)` | modal/popover |

## Motion

| Token | Value | Curve | Use |
|-------|-------|-------|-----|
| `--duration-fast` | 150ms | ease-out-quart | hover/focus/toggle |
| `--duration-base` | 250ms | ease-out-quart | panel mount, modal open |
| `--duration-slow` | 400ms | ease-out-quart | orchestrated reveals |

尊重 `prefers-reduced-motion: reduce`。

## Surface Vocabulary (5 层)

1. **Base** (`bg-base`) — 页面底色，最暗
2. **Surface** (`bg-surface`) — 面板/卡片默认底，微微透
3. **Elevated** (`bg-elevated`) — 浮层/modal，实色偏暗
4. **Inset** (`bg-inset`) — 嵌套区域，更暗
5. **Overlay** (`overlay`) — backdrop 暗幕

## Component Library (核心)

### Shell & Navigation
- `GlobalSidebar` — 左侧全局导航 (Workspace/Library/Settings)
- `PipelineSidebar` — 项目内步骤导航 (Script/ArtDirection/Cast/Storyboard/Assembly)
- `BreadcrumbBar` — 层级面包屑

### Surfaces
- `GlassPanel` — 克制版（极低透明度 + 1px border，不滥用 blur）
- `Card` — series/project/episode 卡片
- `SectionShell` — 模块内 section 容器（chrome header + 操作区 + content）

### Inputs & Controls
- `Button` (primary / glass / ghost / destructive) + `IconButton` + `Tooltip`
- `Input` / `Textarea` / `Select` / `SegmentedControl`
- `FieldTagChip` — prompt 标签
- `VariantSelector` / `VideoVariantSelector`

### Storyboard Workbench
- `ShotCard` — 分镜卡片（缩略 + 状态 + 参考资产 chips + 操作）
- `ShotPanel` — 展开的分镜详情面板
- `PolishPanel` — Prompt 润色对比面板
- `CompareModal` / `Lightbox` — content-first 大图/视频对比
- `TaskQueueButton` + `TaskQueuePanel` — 生成队列
- `CandidatesSection` / `CandidateThumb` — 候选结果网格
- `StatusBadge` — 5 状态标记
- `StepHeader` — 统一步骤标题栏

### Feedback
- `Toast` (success/error/info/warning)
- `Progress` (determinate/indeterminate)
- `Skeleton` — 加载占位
- `EmptyState` — 空态引导

## Atelier 对接预留

- Atelier 复用 `--seed-*` 层，在其 graph-first shell 中建立独立 `--atelier-*` 语义层
- 共享字体/motion/status token 不变
- Canvas 节点/边样式由 Atelier 自身定义，但 card/button/input 基础组件复用 DS 通用层
- 未来拆包路径: `packages/lumenx-design-tokens/` 导出 seed + semantic 两层

## Agent Prompt Guide

生成或修改工作区文件前：
1. 读取本 `DESIGN.md`、`design-system.css`、`design-system.json`
2. 颜色/字号/间距必须走 token，不允许 hardcode hex
3. 新组件必须声明 `data-component` 属性
4. 状态必须覆盖：default / hover / focus-visible / active / disabled / loading
5. 完成后跑 Canvas artifact lint，修复 P0/P1 再交付
