# LumenX 前端主题化 + Playground 还债 — 后端侧实施规划

> 落地方：项目原 Agent（我）。设计方：Tasty Sam（handoff 已交付）。
> 本文档是我这侧的执行计划 + 关键决策登记。每完成一个 Phase 即 commit（遵循 atomic commit 规则）。

---

## 0. 侦察结论（已核实，纠正 handoff 偏差）

| handoff 声明 | 实测 | 结论 |
|---|---|---|
| 现有前端 100% 走 CSS 变量、组件零改动 | 全局 token:hex ≈ 20:1（95% 已 token 化）✅；**但 Playground 0% token 化** ❌ | 对老代码成立，**对 Playground 不成立** |
| 只需变量化 tailwind primary/secondary/accent | 属实，L26-28 是硬编码 hex | ✅ |
| tokens.css drop-in 并入 globals.css | semantic+字体层 drop-in；但有 3 个接线点（选择器机制、双 `:root` 默认、tailwind 主色） | 半真 |
| FUNCTION-COVERAGE 🔴 清零 | 属实，active 🔴=0；但 ~30 个 🟡 接线工作量在此 | ✅ |

**Playground 债务量化**：88 处 arbitrary-hex + 338 处 `white/black` 透明度类，12 文件，0 处语义类。

**测试基建**：仅 Vitest（node/happy-dom，不渲染 CSS）。**零视觉回归能力**——主题改坏会通过所有现有检查。

---

## 1. 执行顺序（含硬依赖）

> 用户决策：先还 Playground 债。但 Playground 还债依赖语义 token 先就位（`bg-[#646cff]→bg-primary` 需要 `--color-primary` 存在 + tailwind 变量化），否则白改。故实际序为 **Phase 0 地基 → Phase 1 Playground**。

### Phase 0 · 地基（不改任何页面外观，纯让 token 体系就位）

目标：现有 dark 外观**像素级不变**（默认主题观感不变），但 5 主题 token 全部就位、tailwind 主色可翻转。

改动文件（全部来自 handoff §4 + §7，无新组件）：

1. **`globals.css`** — 并入 tokens.css 的 5 主题：
   - 选择器 `:root[data-theme="<id>"]` → `html.<id>`
   - 默认 `:root` 复用 `atelier-dark`（handoff 指定默认）
   - 补 `--color-primary/-primary-hover/-accent/-accent-hover/-on-accent` + 氛围变量（`--halation/--bloom/--glow-*/--grain-op`）+ `--font-display`(Fraunces) + component 别名
   - 合并字体 @import（加 Fraunces）
   - **保留**现有 `--foreground-rgb/--background-*-rgb`（body 渐变在用，tokens.css 没有）
2. **`tailwind.config.ts`** — `primary/secondary/accent` 硬编码 hex → `var(--color-primary/secondary/accent)`。⚠️ tokens.css **没定义 `--color-secondary`**，需补一个映射（建议 `--color-secondary: var(--seed-primary-2)` 或单列）。
3. **`settingsStore.ts`** — `Theme = 'dark'|'light'` → `ThemePreset`（5 值），默认 `'atelier-dark'`；persist 加 `version:1` + `migrate`（旧 `'dark'→'brand-dark'`、`'light'→'brand-light'`）。
4. **`Providers.tsx`** — class 切换从 2 值扩到 5 值（remove 全部 5 个 + add 当前）。
5. **`layout.tsx`** — SSR 默认 `className="dark"` → `"atelier-dark"`；防闪烁 IIFE 白名单扩 5 值 + 默认 atelier-dark。
6. **`SettingsPage.tsx`** — 主题切换从 2 段按钮 → 5 张预设卡（参考三页右上角 `.themepick`）。

**Phase 0 验证**：
- typecheck + build 通过
- gstack 截图：默认主题（atelier-dark）下 storyboard/workspace/library/settings 四页正常
- 切 5 主题，模范生页面（非 playground）正确翻转底色/主色/字体

> ⚠️ **关键决策点 A（需你知情）**：handoff 默认 = `atelier-dark`（暖石墨+teal），这会**改变现有产品基线 dark 外观**（原 brand-dark 冷黑+蓝 → 暖石墨+teal）。这是 handoff 的明确设计意图（"Line B 原生皮肤为默认"），用户已说"默认所有人到 atelier-dark"，故照办。**实际 migrate 行为**：`version < 1` 或 theme 不在 5-preset 白名单的持久化值，**一律回落 `atelier-dark`（DEFAULT_THEME）——含老用户的旧 `'dark'`**。即老用户首次启动会从冷黑+蓝切到暖石墨+teal（**观感切换，已确认可接受**），之后可在设置页自由切回任一主题（含 brand-dark 还原原观感）。

### Phase 1 · Playground 还债（我的技术债，优先级最高）

12 文件，约 471 处替换。映射规则（侦察已给出，语义风险低）：

| 硬编码 | → 语义 token |
|---|---|
| `bg-[#646cff]` (×70) | `bg-primary` |
| `hover:bg-[#535bf2]` | `hover:bg-primary-hover`（需 tailwind 加 primary-hover）|
| `#7a82ff/#b9bdff` 等 primary 深浅 | `primary` + 透明度 |
| `bg-[#050508]` | `bg-background` |
| `bg-[#141416]/#0e0e11` | `bg-elevated` / `bg-surface` |
| `from-[#1a1a2e] to-[#0f0f1a]` | 渐变占位 → `bg-surface`（或保留为媒体占位）|
| `text-white` / `text-white/40` | `text-foreground` / `text-text-muted` |
| `border-white/[0.08]` | `border-glass-border` |
| `bg-white/[0.04]` | `bg-glass` / `bg-hover-bg` |
| `#ec4899/#a855f7` 粉紫 | `accent` |

重灾区文件：`ResultCard.tsx`(14) / `GalleryView.tsx`(12) / `AssetPickerModal.tsx`(12) / `PromptTemplateModal.tsx`(11) / `MediaInput.tsx`(8) / `PlaygroundPage.tsx` / `ParameterBar.tsx` / `DetailPanel.tsx`(各6)。

**Phase 1 验证**：
- 守卫脚本：Playground 目录 grep `bg-\[#` / `text-white/` 应趋近 0
- gstack 截图：Playground 在 5 主题下逐一截图，重点看亮色主题（对比度、白底白字）+ 功能态（生成中/失败/完成卡片、详情面板、画廊、各 modal）
- 功能回归：生成/收藏/下载/重试/删除/模板/历史 全部仍可用（接口零改动，只改色）

### Phase 2+（本次先不做，等你看完 Phase 0-1 效果再定）

- shared / cast / storyboard-r2v 的 171 white-alpha 批量转
- Logo 联动（LumenXBranding 按 preset 切 src + filter）
- 模态换肤微调

---

## 2. 验证方法（用户定：A + B）

### A. gstack browse 截图巡检
- 每 Phase 完成后，我驱动 headless 浏览器，逐主题 × 关键页截图
- 人眼/AI 审：色差、对比度、白底白字、玻璃态、状态色
- Phase 1 重点：Playground × 5 主题 × 各功能态

### B. 守卫脚本（防硬编码回潮）
- 写 `scripts/check-no-hardcoded-colors.mjs`（或 eslint 规则）
- 规则：`modules/playground/**` 不允许出现 `bg-[#` / `text-[#` / `border-[#` / `text-white/` / `bg-white/` / `bg-black/`
- 接入 `npm run lint` 或单独脚本，CI 可调用

---

## 3. 冻结清单（遵守 handoff §11，绝不顺手改）

- ❌ 不碰 `api.ts` 任何端点签名
- ❌ 不改数据流方向（StoryboardR2V 仍是唯一 orchestrator）
- ❌ 不改轮询/防抖时序、localStorage 键、buildAssembledPrompt 规则
- ❌ 不改任务端点异步语义
- ❌ 不为迁就设计删减功能
- ✅ 仅做：颜色 hex → 语义 token、store 枚举升级、token 体系并入

---

## 4. 关键决策登记（需你知情/拍板）

| # | 决策 | 我的处置 | 状态 |
|---|---|---|---|
| A | 默认主题改为 atelier-dark（改变基线观感）| 照 handoff；migrate 把任何旧/非法值（含老用户旧 'dark'）统一回落 atelier-dark；老用户首次启动观感切换，可接受，可在设置页切回 | 已定（你说"默认所有人到 atelier-dark"）|
| B | tailwind 缺 `--color-secondary` | 补 `--color-secondary: var(--seed-primary-2)` | 待你确认或我自定 |
| C | body 渐变用的 `--foreground-rgb` 等 tokens.css 没有 | 保留现有定义，不被覆盖 | 我自处理 |
| D | Phase 2+ 是否本轮做 | 先交付 Phase 0+1，你看效果再定 | 待定 |

---

## 5. 实施中发现（Phase 0）

### 发现 1：`@apply` 上下文不支持 var 色 + alpha（已解决，非架构级）
- 现象：`primary` 从 hex 改为 `var(--color-primary)` 后，build 报 `globals.css` 里 `.glass-input` 的 `@apply ... focus:border-primary/50` "class does not exist"。
- 初判：担心 376 处 `primary/N` 透明度类全失效（架构级）。
- **核查推翻初判**：代码库早有 `bg-surface/50`、`border-glass-border/50`、`text-foreground/50` 等 var 色 + alpha 用法在正常工作（这些色本来就是 var）；且去掉 `@apply` 单点后 `npm run build` 成功 → 证明 **className 里的 var+alpha 在 Tailwind 3.4 用 color-mix 正常降级**。
- 真因：仅 **`@apply` 指令** 对 var 色 + alpha 更严格（Tailwind 3.4 限制），className 不受影响。
- 修复：`globals.css` 的 `.glass-input:focus` 改用原生 `color-mix(in srgb, var(--color-primary) 50%, transparent)`，单点修复，零视觉变化。**不需改 token 结构，不需回找设计方。**
- 结论：376 处 `primary/N` 无需处理；Phase 0 地基干净落地，build 通过。

### 发现 2：CreativeCanvas 的 `theme === "dark"` 回归（已修）
- Phase 0 把 theme 枚举从 `'dark'|'light'` 升级为 5 preset，但 `CreativeCanvas.tsx` 的 `const isDark = theme === "dark"` 漏改 → 永远 false，所有新主题下 3D 背景走浅色分支。
- 修复：`theme.endsWith("-dark")`。

### 发现 3：headless 无 GPU → Three.js 崩溃 → 整页白屏（已加容错，用户批准）
- 截图验证时 headless Chromium 无 WebGL，`CreativeCanvas` 的 R3F `<Canvas>` 创建 WebGL 失败抛错，冒泡使整个 `page.tsx` React tree 崩溃，截不到 UI（也使 Providers 无法设 html class）。
- 这同时是**真实生产隐患**：用户禁用硬件加速/WebGL 时会整站白屏。
- 修复：`CreativeCanvas` 加 `detectWebGL()` 检测（`useState(false)` + `useEffect`，SSR/client 首屏一致以避免 hydration mismatch）+ `CanvasErrorBoundary` 兜底。WebGL 不可用时退化为静态主题底色背景（外层 `bg-background` + CSS 渐变层），不崩整页。纯展示层加固，不碰数据流/API/orchestrator。真实用户首屏 3D 背景延迟一帧渐入（底色始终在），可接受。

### Phase 0 验证结论（A+B 的 A：截图巡检）
- **数值**（headless 手动切 className 读 CSS 变量，绕过 WebGL/React）：5 主题 `--color-primary`/`--color-accent`/`--seed-base`/`--font-display` 全部正确翻转。
  | 主题 | primary | accent | seed-base | font |
  |---|---|---|---|---|
  | atelier-dark | #34d8c4 | #ffa94d | #0c0b0e | Fraunces |
  | bridge-dark | #646cff | #ffa94d | #0a0a0d | Space Grotesk |
  | brand-dark | #646cff | #ff0080 | #050508 | Space Grotesk |
  | atelier-light | #1d9c8d | #e8852b | #f6f1e9 | Fraunces |
  | brand-light | #646cff | #ff0080 | #f8f9fa | Space Grotesk |
- **视觉**（加 WebGL 容错后 headless 截图）：5 主题截图巡检通过，底色/主色/字体翻转正确，对比度良好，无白底白字（除下方 logo 待办）。截图存 `/private/tmp/lumenx-theme-shots/`。
- **测试**：`settings-store.test.ts` 更新到新 ThemePreset + 遍历断言 + preset 列表断言，6 tests passed；typecheck 中 CreativeCanvas/settings-store 干净（仅剩 pre-existing EnvConfig/ModelSettings 错误，与主题无关）。

### 遗留待办（Phase 2 logo 联动，非 Phase 0 阻塞）
- 浅色主题下 sidebar logo 的 "LUMEN" 文字为 `text-white` 硬编码 → 白底白字隐形；logo 图标/"X" 颜色也未随 preset 切换。

### 验证基建踩坑记录（供 Phase 1 复用）
- gstack browse 首次需 `~/.claude/skills/gstack/setup` 装 Playwright chromium v1208（与 dist/browse 配套）。
- 截图路径必须落在 `/private/tmp` 或项目根内（job tmp 目录被拒）。
- Next dev 跑久了（本次 6 天）会编译产物漂移 + chunk 404，验证前最好重启前端 dev server。
- `wait --networkidle` 在 Next dev 必卡（HMR websocket 永不 idle），改 `wait --load` + sleep。
- browse `--headed` 在本后台环境 daemon 配置切换死锁；headless + CreativeCanvas WebGL 容错是更稳的视觉验证路径。
