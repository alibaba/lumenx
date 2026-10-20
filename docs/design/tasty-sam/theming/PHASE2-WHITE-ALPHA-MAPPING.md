# Phase 2 — white/black-alpha → 语义 token 裁决表

> **范围**：`frontend/src/components/**`（不含 `playground/**`，那 12 个文件已在 Phase 1 清零）。
> **量化**：`white/N` 188 处 / 33 文件；`black/N` 122 处 / 38 文件。
> **目标**：所有 `(bg|text|border|from|to|via|ring|divide|placeholder|outline|caret|decoration|accent|ring-offset)-white/(N|[0.x])` 与同类 `black/N` 全部换成语义 token；扩展守卫脚本覆盖全 `components/`。
> **不破坏**：HANDOFF §11 冻结（api 签名、数据流、轮询/防抖时序、localStorage 键、buildAssembledPrompt、任务异步语义）。

---

## 0. 现有 token 武器库（落地前对表）

来自 `frontend/src/app/globals.css` × `tailwind.config.ts`（5 主题已全部就位）：

| Tailwind 类 | CSS 变量 | 暗主题取值 | 亮主题取值 |
|---|---|---|---|
| `bg-background` | `--color-bg-base` | 主题 base | 主题 base |
| `bg-surface` | `--color-bg-surface` | 一层抬升 | 一层抬升 |
| `bg-elevated` | `--color-bg-elevated` | 二层抬升 | 二层抬升 |
| `bg-input-bg` | `--color-bg-input` | 输入框底 | 输入框底 |
| `bg-hover-bg` | `--color-bg-hover` | 通用 hover | 通用 hover |
| `bg-glass` | `--color-glass` | 4.5–5% 白雾 | 55% 白雾 |
| `bg-surface-inset` | `--color-bg-inset` | 凹陷面 | 凹陷面 |
| `bg-overlay` | `--color-overlay` | 浮层底 | 浮层底 |
| `border-glass-border` | `--color-border-default` | 6–8% 白 / 12% 墨 | 12% 墨 / 10% 黑 |
| `border-border-subtle` | `--color-border-subtle` | 3–4% 白 / 7% 墨 | 7% 墨 / 6% 黑 |
| `text-foreground` | `--color-text-primary` | 主文本 | 主文本 |
| `text-text-secondary` | `--color-text-secondary` | 次文本 | 次文本 |
| `text-text-muted` | `--color-text-muted` | 弱文本/占位 | 弱文本/占位 |
| `text-primary` / `bg-primary` | `--color-primary` | 主色 | 主色 |
| `text-accent` / `bg-accent` | `--color-accent` | 强调 | 强调 |
| `text-on-accent` / `text-on-warm` | `--color-on-*` | accent 上的字 | accent 上的字 |

> **关键事实**：`var(...)` 色 + Tailwind `/alpha` 在 Tailwind 3.4 的 className 上下文里通过 `color-mix(in srgb, var(--x) N%, transparent)` 正常工作（已在 Playground 还债中验证 376 处 `primary/N` 全部生效）。**`@apply` 上下文除外**，那是边缘 case，单点改 `color-mix(...)` 即可。

---

## 1. 模式 → token 映射规则（按用途分类，按规则裁决）

> 落地法则：**永远先按"语义用途"分类**，再套规则。同一段 `bg-white/[0.04]` 在不同语义下落不同 token。

### 规则 A — 表面/容器底（最常见，约占 40%）

| 当前写法 | → 替换 | 说明 |
|---|---|---|
| `bg-white/[0.015]` / `bg-white/[0.02]` | `bg-glass`（或 `bg-surface`） | 极浅毛玻璃面，做行/卡片底 |
| `bg-white/[0.03]` / `bg-white/[0.04]` | `bg-glass` 或 `bg-elevated` | 静态卡片/输入框底；若需更亮抬升用 elevated |
| `bg-white/[0.05]` / `bg-white/[0.06]` | `bg-hover-bg` | **若是 hover 状态**；若是静态卡片用 `bg-elevated` |
| `bg-white/[0.08]` | `bg-elevated` | 二层抬升/选中态背景 |
| `hover:bg-white/[0.04]` / `hover:bg-white/[0.06]` | `hover:bg-hover-bg` | 所有 hover 一律 hover-bg |

**裁决要点**：
- 是不是 `hover:` 前缀？→ 一律 `hover:bg-hover-bg`
- 是不是 `selected/active` 状态？→ `bg-elevated`（亮一阶）
- 是不是行/卡片"贴底色"？→ `bg-glass`（透明感）或 `bg-surface`（实色感）；优先 glass 保毛玻璃语义

### 规则 B — 边框（约 25%）

| 当前写法 | → 替换 | 说明 |
|---|---|---|
| `border-white/[0.03]` / `border-white/[0.04]` | `border-border-subtle` | 极浅分隔线 |
| `border-white/[0.05]` ~ `border-white/[0.10]` | `border-glass-border` | 标准卡片/输入框边框 |
| `border-white/[0.12]` ~ `border-white/[0.20]` | `border-foreground/15` | 强调边/选中态；保留 alpha 以保层次 |
| `border-t-white/[0.06]` / `border-b-white/[0.06]` | `border-t-glass-border` / `border-b-glass-border` | 同上，带方向时 |
| `divide-white/[0.06]` | `divide-glass-border` | 列表分隔 |
| `hover:border-white/15` / `hover:border-white/30` | `hover:border-foreground/30` | hover 强调边框 |

**裁决要点**：
- `<= 0.05` → `border-border-subtle`
- `0.06 ~ 0.10` → `border-glass-border`
- `>= 0.12` → `border-foreground/N`（保留 alpha 表达强弱）

### 规则 C — 文字（约 20%）

| 当前写法 | → 替换 | 说明 |
|---|---|---|
| `text-white/15` / `text-white/20` | `text-text-muted` | 占位/极弱辅助 |
| `text-white/25` / `text-white/30` / `text-white/40` | `text-text-muted` | 弱次级文本（label、单位、说明） |
| `text-white/50` / `text-white/60` | `text-text-secondary` | 次级文本 |
| `text-white/70` / `text-white/80` | `text-foreground/80` | 次主文本 |
| `text-white/90` | `text-foreground` | 等同主文本 |
| `hover:text-white/60` / `hover:text-white/80` | `hover:text-foreground` | hover 升主 |
| `placeholder:text-white/15` ~ `/30` | `placeholder:text-text-muted` | 占位文本 |

**裁决要点**：所有"小字 + 低 alpha"统一 `text-text-muted`；中段 `text-text-secondary`；hover/活跃态升到 `text-foreground`。不要再造 `text-foreground/30 text-foreground/40` 阶梯，**用 muted/secondary 两档**。

### 规则 D — 渐变与发光（约 10%）

| 当前写法 | → 替换 | 说明 |
|---|---|---|
| `from-white/[0.06] to-transparent` | `from-glass-border to-transparent` | 分割渐变 |
| `via-white/[0.06]` | `via-glass-border` | 中段分割 |
| `bg-gradient-to-r from-white/[0.06] via-white/[0.10] to-white/[0.06]` | `bg-gradient-to-r from-glass-border via-foreground/15 to-glass-border` | 进度条/装饰条 |
| `shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]` | **保留**（功能性内阴影发光） | 不算颜色 token 范围 |
| `shadow-[0_0_80px_rgba(255,255,255,0.04)]` | **保留** | 同上 |

### 规则 E — 黑色透明（功能性遮罩，约多数应保留）

> Playground 守卫脚本现行规则：**允许 `black/N`**（功能性遮罩，图片角标、modal backdrop、底部渐变）。同规则迁移到全局。

| 当前写法 | 是否换 | 说明 |
|---|---|---|
| `bg-black/20` ~ `bg-black/60`（图片底部、缩略图） | **保留** | 功能性遮罩，跨主题语义不变 |
| `bg-black/55` / `bg-black/80`（modal backdrop / lightbox） | **保留** | 浮层底纹 |
| `from-black/60 to-transparent`（图片底部 caption 渐变） | **保留** | 功能性渐变 |
| `border-black/N`（极少数，多在亮主题硬编码） | **改** → `border-foreground/N` | 跨主题不对 |
| `text-black/N` | **改** → `text-foreground/N` 或 `text-text-secondary` | 亮主题下白底白字风险 |

**裁决要点**：`bg-black/N` 99% 保留；`text-black/N` 与 `border-black/N` 一律换。

### 规则 F — 例外白色（保留 `text-white`、`bg-white`）

> Playground 守卫脚本现行：**允许纯 `text-white`（无 alpha）**，因为它常用在彩色按钮 / 强 accent 背景上，主题翻转时按钮底色仍是深的，白字仍可读。

| 当前写法 | 是否换 | 说明 |
|---|---|---|
| `text-white`（在 `bg-primary` / `bg-accent` / `bg-gradient-...` 上） | **保留** | 彩色按钮文本 |
| `text-white`（在毛玻璃/中性背景上） | **改** → `text-foreground` | 亮主题白底白字 |
| `bg-white`（极少，几乎只在 demo 里） | 视情况，多半改 | 罕见 |

**自检方法**：grep 命中 `text-white` 后，看同一 className 是否同时含 `bg-primary` / `bg-accent` / `bg-rose-` / `bg-emerald-` 等饱和色背景；含则保留，否则换。

---

## 2. 文件优先级（按 white/N 出现次数倒序）

> 主张：**重灾区先动**，每个文件单独一个 commit，跑一遍 build + typecheck + 5 主题截图。

### 一类：单文件 ≥10 处（5 个文件，74 处，39%）

| # | 文件 | white/N | black/N | 备注 |
|---|---|---|---|---|
| 1 | `modules/storyboard-r2v/VideoConfigModal.tsx` | 37 | 1 | 视频参数模态，规则 A/B/C 均涉及，最复杂 |
| 2 | `modules/storyboard-r2v/ShotCard.tsx` | 20 | 6 | 核心镜头卡，hover/selected 态密集 |
| 3 | `shared/PendingTaskAffordance.tsx` | 14 | 7 | 任务挂起态横幅，`bg-black/35` 多个保留 |
| 4 | `modules/ArtDirection.tsx` | 13 | 1 | 美术方向模块 |
| 5 | `modules/cast/CastWorkbenchModal.tsx` | 10 | 6 | 角色工作台模态 |

**这一类必须逐文件人工审，不能批量 sed**。每文件预算 30–60 分钟。

### 二类：单文件 5–9 处（9 个文件，约 70 处，37%）

| 文件 | white/N | black/N |
|---|---|---|
| `modules/storyboard-r2v/FieldTagChip.tsx` | 8 | 1 |
| `modules/storyboard-r2v/AssetDrawer.tsx` | 8 | 1 |
| `modules/storyboard-r2v/shot-panel/CompareModal.tsx` | 8 | 3 |
| `modules/Cast.tsx` | 8 | 7 |
| `series/SeriesArtDirectionPanel.tsx` | 7 | 1 |
| `shared/preview/LightboxProvider.tsx` | 6 | 7 |
| `modules/storyboard-r2v/DialogueAudioRow.tsx` | 6 | 9 |
| `modules/storyboard-r2v/shot-panel/ParamsSection.tsx` | 5 | 5 |
| `modules/MotionTabContent_simplified.tsx.snippet` | 4 | 4 |

> **注意**：`MotionTabContent_simplified.tsx.snippet` 是 snippet，看代码库是否实际引用。若不引用，**删除即可，不做 token 化**。

**这一类可半自动**：先用上面规则表批量替换，再人工 review。每文件 10–20 分钟。

### 三类：单文件 1–4 处（19 个文件，约 44 处，24%）

```
modules/cast/VoicePickerModal.tsx           4
modules/storyboard-r2v/PolishPanel.tsx       3
modules/storyboard-r2v/PromptExpandModal.tsx 3
modules/storyboard-r2v/shot-panel/T2ISubsection.tsx 3
modules/storyboard-r2v/shot-panel/CandidateThumb.tsx 2
modules/storyboard-r2v/GenerationBanner.tsx  2
modules/VideoAssembly.tsx                    2
modules/StoryboardR2V.tsx                    2
modules/EntityConfirmModal.tsx               2
shared/preview/PreviewVideo.tsx              2
shared/preview/PreviewImage.tsx              1
modules/storyboard-r2v/shot-panel/CandidatesSection.tsx 1
modules/storyboard-r2v/shot-panel/TaskQueueButton.tsx   1
modules/storyboard-r2v/shot-panel/TaskQueuePanel.tsx    1
shared/StepHeader.tsx                        1
modules/ConsistencyVault.tsx                 1
modals/UploadAssetModal.tsx                  1
modules/cast/VoiceCloneModal.tsx             1
common/GroupedModelGrid.tsx                  1
```

**这一类纯机械**，可一次 commit 全清。每文件 < 5 分钟。

---

## 3. 落地执行节奏（推荐）

```
PR-1: 三类小文件批量清理（19 文件 / ~44 处，1 commit）
       ↓ 截图 + typecheck + build
PR-2: 二类中等文件（9 文件，每文件 1 commit 或合并为 2-3 commit）
       ↓ 5 主题截图巡检
PR-3: 一类重灾区（5 文件，每文件独立 commit）
       VideoConfigModal → ShotCard → PendingTaskAffordance → ArtDirection → CastWorkbenchModal
       ↓ 每 commit 后 5 主题 × 该模态/卡 单独截图，验对比度
PR-4: 扩展守卫脚本 + 清理 MotionTabContent_simplified.tsx.snippet（若未引用直接删）
```

---

## 4. 守卫脚本扩展（防回潮）

现有 `frontend/scripts/check-playground-colors.mjs` 仅扫 `playground/`。Phase 2 落地完即扩展为全 `components/` 扫描，作为 lint/pre-commit 必跑：

**新脚本路径建议**：`frontend/scripts/check-no-hardcoded-colors.mjs`

**规则**：
- 全 `frontend/src/components/**/*.tsx` 扫描
- 禁：`arbitrary hex`（`[#xxx]`） + `(bg|text|border|from|to|via|ring|divide|placeholder|outline|caret|decoration|accent|ring-offset)-white/(N|[0.x])`
- 允许：纯 `text-white`（无 alpha）、所有 `black/N`（除 `text-black/N`、`border-black/N`）
- 允许白名单（per-file）：`shared/preview/LightboxProvider.tsx` 的 backdrop 类、`shared/preview/PreviewImage.tsx` 的 caption 渐变（可加 `// allow-black-alpha` 行注释豁免）

**接入**：
- `package.json` 加 `"check:colors": "node scripts/check-no-hardcoded-colors.mjs"`
- 加入 `lint:strict` 或 `prebuild`
- CI 必跑

---

## 5. 风险/疑难案例（落地时如遇歧义先找 Tasty Sam）

### 5.1 `bg-white/[0.04]` 在 hover 还是静态？

看 className 是否被 `hover:` 前缀包；不被包但同组件内有 `hover:` 兄弟 → 多半是 **selected/active** 态，用 `bg-elevated`。

### 5.2 `border-white/8`（非中括号语法）

Tailwind 默认刻度只有 `/5 /10 /20 /25 /30 /40 /50...`；`/6 /8` 通常是 arbitrary（实测 `border-white/8` 等价 `border-white/8%`）。按规则 B 落 `border-glass-border`，丢失 1–2% 不影响视觉。

### 5.3 内联 `shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]`

**不算颜色 token 范围**，保留。这是发光质感，跨主题语义一致（暗色"高光"在亮色上也是合理的微微反光）。除非视觉评审说亮主题刺眼，再单独换。

### 5.4 `bg-red-500/[0.05]` / `text-rose-400` / `bg-amber-500/N`

**功能态色（error/warning）**，不在本表范围。已有 `status-failed-bg/-fg/-border` 等语义 token，建议**单开一个小批次**统一换，与 white-alpha 解耦。

### 5.5 `MotionTabContent_simplified.tsx.snippet`

确认是否被任何 `import` 引用。若否，**直接 git rm**，不做替换。

---

## 6. 验证清单（每个 PR 完成前必跑）

- [ ] `cd frontend && npm run typecheck` 通过
- [ ] `cd frontend && npm run build` 通过
- [ ] `node scripts/check-no-hardcoded-colors.mjs`（脚本就绪后）返回 0 违规
- [ ] gstack 5 主题 × 受影响页/模态截图（atelier-dark / bridge-dark / brand-dark / atelier-light / brand-light）
- [ ] 亮主题重点查：是否有白字、低对比、边框消失、毛玻璃错乱
- [ ] 暗主题重点查：色差是否仍保留层次，没有压扁成一片
- [ ] 涉及交互的（hover/active/focus/selected）四态全部检
- [ ] 不引入新依赖、不改 className 之外的逻辑/数据流

---

## 7. 与 HANDOFF §11 冻结清单的契合

本批次**只**做：

✅ className 颜色硬编码 → 语义 token
✅ 守卫脚本扩展
✅ 未引用的 snippet 删除

**不**做：

❌ 任何 props / state 重构
❌ 任何 hook / effect 改动
❌ 任何 api.ts / store / orchestrator 接线变化
❌ 任何组件拆分/合并
❌ 任何动效/布局/字号调整（除非视觉评审明确要求）

落地 Agent 凡是发现"顺手"会动到上述黑名单的，**立即停手，找 Tasty Sam 复审**。
