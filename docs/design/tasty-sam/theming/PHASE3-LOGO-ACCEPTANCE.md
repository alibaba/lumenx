# PHASE 3 · Logo 联动验收清单（Tasty Sam → 落地 Agent）

> 目的：把 `logo-adaptation.html` 的设计意图固化为 **pixel-level 可勾验收项**，让 Logo 随主题联动落地有唯一参照，杜绝亮色白底白字、teal 着色漏配、资产 404。
> 真相源：`logo-adaptation.html`（设计稿）+ `frontend/src/components/layout/LumenXBranding.tsx`（落地组件）。
> 状态图例：✅ 已落地（本轮核验通过）｜🟡 待补｜⚠️ 需关注。

---

## 0. 重大现状（核验后更新）

**`LumenXBranding.tsx` 已提前完成改造**（工作区 modified，尚未 commit）。本清单从"待落地"转为"**落地验收 + 收尾**"。核验结论：组件实现与设计稿一致，仅剩资产提交、视觉巡检、暗色 logo 命名三处收尾。

---

## 1. Logo × 5 主题联动矩阵（验收基准表）

| 主题 preset | logo src（public/）| CSS filter | wordmark 文字色 | "X" 强调色 | 底色 | 组件落地 |
|---|---|---|---|---|---|---|
| `atelier-dark` ★默认 | `/logo-dark.png` | `hue-rotate(-64deg) saturate(1.35) brightness(1.08)` → 蓝染 teal | `text-foreground` | `text-primary`(teal) | #0c0b0e | ✅ |
| `bridge-dark` | `/logo-dark.png` | `none` | `text-foreground` | `text-primary`(蓝) | #0a0a0d | ✅ |
| `brand-dark` | `/logo-dark.png` | `none` | `text-foreground` | `text-primary`(蓝) | #050508 | ✅ |
| `atelier-light` | `/logo-light-teal.png`（深墨描边+teal核）| `none` | `text-foreground` | `text-primary`(teal) | #f6f1e9 | ✅ |
| `brand-light` | `/logo-light.png`（深墨描边+蓝核）| `none` | `text-foreground` | `text-primary`(蓝) | #f8f9fa | ✅ |

> 对照 `LumenXBranding.tsx`：`LOGO_SRC` 映射(L13-19) + `ATELIER_DARK_FILTER`(L21) + `text-foreground`(L49) + `text-primary`(L52) —— **逐格匹配设计稿，✅**。

---

## 2. 逐项验收勾验

### A. 资产就位（防 404）
- [x] `public/logo-dark.png` 存在
- [x] `public/logo-light-teal.png` 存在
- [x] `public/logo-light.png` 存在
- [ ] ⚠️ **三个新 logo 仍是 untracked（`??`）**，必须 `git add` 并随 Phase 3 commit，否则他人 checkout 后白屏 404。

### B. 组件接线
- [x] `src` 按 `presetId` 切换（`LOGO_SRC[activeTheme]`，含 `?? '/logo-dark.png'` 兜底）
- [x] teal filter **仅** `atelier-dark` 生效（`activeTheme === 'atelier-dark'`）
- [x] "LUMEN" 文字 `text-white` → `text-foreground`（消除亮色白底白字 ✅ 旧缺陷已解）
- [x] "X" `#646cff` → `text-primary`（随主题翻 teal/蓝）
- [x] "Studio" / slogan 用 `text-foreground/30`、`/20`（随主题，非硬编码白）
- [x] SSR/client 首屏统一 `atelier-dark` 防 hydration mismatch（`mounted` gate，L30-32）

### C. 引用清理
- [x] 全仓库无 `LumenX-cybr.png` / `LumenX_亮色.png` / `LumenX.png` 旧引用残留（grep 零命中）
- [ ] 🟡 `public/` 下旧资产 `LumenX-cybr.png` / `LumenX-cybr-transparent.png` / `LumenX.png` / `LumenX_亮色.png` 已无人引用，建议确认无其它用途后清理（**非阻塞，删前 grep 全仓**）。

### D. 视觉巡检（gstack headless，落地 Agent 执行）
- [ ] 🟡 5 主题逐一截图侧栏 Logo 区，验：
  - atelier-dark：logo 呈 **teal**（filter 生效），非原蓝
  - bridge/brand-dark：logo 原蓝，无染色
  - atelier-light / brand-light：深墨描边 logo 在亮底 **清晰可读**，"LUMEN" 不隐形
  - "X" 颜色 5 主题各自正确（teal / 蓝）
- [ ] 🟡 两处调用点都验：`PipelineSidebar`(size=sm) + `GlobalSidebar`(size=sm)

---

## 3. ⚠️ 需关注：暗色 logo 命名语义

`atelier-dark` / `bridge-dark` / `brand-dark` 三个暗主题**共用** `/logo-dark.png`（白描边蓝核 PNG），atelier-dark 靠 filter 染 teal。命名 `logo-dark` 准确（= 暗主题用图）。**无需改**，但登记备查：teal 效果是运行时 filter 产物，磁盘上无独立 teal-dark PNG——若未来要去掉运行时 filter 改静态 PNG，需新增 `logo-dark-teal.png` 资产。

---

## 4. 收尾动作（落地 Agent，按序）

1. `git add public/logo-dark.png public/logo-light-teal.png public/logo-light.png`（连同 `LumenXBranding.tsx`）。
2. gstack 5 主题 × 2 侧栏截图巡检（§2.D）。
3. （可选）清理 4 个废弃 logo PNG，删前全仓 grep。
4. commit：建议 `feat(theme): Phase 3 — logo per-preset linkage + acceptance`。

---

## 5. 验收闸门

Phase 3 Logo 部分 **DONE** 条件：§2.A 全勾（资产入库）+ §2.B 全勾（已满足）+ §2.D 视觉巡检通过。
达成后 + Phase 2 white-alpha 扫尾完成 → 开 **架构重构闸门**（`FRONTEND-ARCHITECTURE-AUDIT.md`）。

> 本清单与代码冲突时，以"亮色不得白底白字、teal 仅 atelier-dark、资产必入库"为最高准则。
