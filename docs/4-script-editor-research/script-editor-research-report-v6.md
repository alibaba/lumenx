# LumenX Studio 剧本编辑器重塑调研报告

> **项目**: LumenX Studio (tron-comic)  
> **版本**: v6.0  
> **日期**: 2026-07-03  
> **范围**: 开源工具、竞品、市场趋势、技术选型、前端设计与功能路线图

---

## 一、执行摘要

LumenX Studio 当前的剧本编辑器仍为纯 `<textarea>` 实现，在格式引擎、语法高亮、结构化解析等维度与行业标准存在严重差距。全球剧本软件市场以 17–19% CAGR 高速增长（2025: $186–220M → 2034: $860–1012M），亚太增速最快（19.2%），中国短剧×AI 视频交叉点是核心增量机会。

**核心结论**:
1. **技术选型**: Tiptap + Yjs + Fountain.js 三层架构是最优路径——低上手成本、CRDT 协作就绪、Block-based 扩展性强
2. **竞品洞察**: laper.ai 以 CRDT + Multi-agent AI + 「剧本即数据中心」定位领跑，但缺失视频管线和 API——LumenX 的核心差异化
3. **优先级**: P0 格式引擎 → P1 结构化 DB + AI 嵌入 + 导出 → P2 协作 → P3 完整生态
4. **设计方向**: 从「空白画布」升级为「沉浸式创作座舱」——Block 编辑 + 侧边面板 + AI 气泡 + 深色主题一致性

---

## 二、现状评估

### 2.1 前端实现 (ScriptProcessor.tsx)

| 维度 | 现状 | 评级 |
|------|------|------|
| 编辑形态 | 纯 `<textarea>` | 无结构化 |
| 持久化 | onChange→Zustand; onBlur→PUT API | 合理 |
| 实体提取 | 点击→LLM→确认弹窗 | 交互割裂 |
| 格式感知 | 无 | — |
| 语法高亮 | 无 | — |
| Fountain 支持 | 无 | — |
| 大纲导航 | 无 | — |
| 实时预览 | 无 | — |
| 协作 | 无 | — |
| 统计面板 | 无 | — |

### 2.2 后端实现 (llm.py)

| 能力 | 现状 |
|------|------|
| 实体提取 | `parse_novel()` 完整 LLM 提取 |
| 分帧生成 | `analyze_to_storyboard()` |
| 规则解析 | 无 (纯 LLM) |
| Fountain/FDX | 无 |
| 数据模型 | `original_text: str` 纯文本 |

### 2.3 已有优势

1. **两阶段实体提取** (extractPreview → confirm → reparse)
2. **系列感知** (ReconcileModal 跨集合并 + 前情提要)
3. **完整视频管线** (剧本→分镜→资产→视频合成)
4. **两层保存** (Zustand + blur 持久化)
5. **完整 REST API** (可编程集成)

### 2.4 缺口诊断矩阵

| 功能 | 行业标准 | LumenX 现状 | 差距 | 优先级 |
|------|---------|-----------|------|--------|
| 格式引擎 | 自动排版 | 无 | 严重 | P0 |
| 语法高亮 | 元素区分 | 无 | 严重 | P0 |
| Fountain 解析 | 结构识别 | 无 | 严重 | P0 |
| 格式导出 | FDX/PDF/Fountain | 无 | 严重 | P1 |
| 内联 AI | 上下文续写 | 无 | 重要 | P1 |
| 结构化 DB | 实时同步 | 手动 | 重要 | P1 |
| 版本历史 | 变更追踪 | 无 | 中等 | P2 |
| 协作 | 实时多人 | 无 | 中等 | P3 |

---

## 三、主流开源工具对比

### 3.1 Fountain 生态

Fountain 是纯文本剧本格式标准 (2012)，核心哲学「Any text editor on any device」。适配中文需扩展 `内景.`/`外景.` 前缀 + LLM 兜底。

### 3.2 工具能力矩阵

| 工具 | 语言 | 核心能力 | FDX | 许可 | LumenX 价值 |
|------|------|---------|-----|------|------------|
| **Fountain.js** | JS | AST 解析 + HTML 输出 | 否 | — | ★★★ 前端集成 |
| **screenplay-tools** | C++/JS/Py/C# | 跨语言模型 + Fountain↔FDX | 是 | — | ★★★ 数据模型 |
| **BetterFountain** | VSCode | 高亮+补全+大纲+预览+统计+PDF | 否 | MIT | ★★★ 交互金标准 |
| **Afterwriting** | Web | 编辑+PDF+分析 | 入 | 开源 | ★★ 分析面板 |
| **Trelby** | Python | 格式强制+分页+20万名库 | 是 | GPL | ★★ 桌面参考 |
| **KIT Scenarist** | 跨平台 | 卡片看板+AI+全格式 | 是 | Free | ★★ 大纲+多格式 |

### 3.3 关键结论

1. Fountain 是零成本高价值起点
2. BetterFountain 是编辑器交互金标准 (实时预览 + 大纲 + 统计 + 时长估算)
3. Arc Studio Pro 代表云端现代形态 (离线+自动排版+beat board+实时协作)
4. screenplay-tools 提供可复用的跨语言数据模型

---

## 四、竞品分析 — laper.ai

### 4.1 产品定位

- **Slogan**: 「From First Draft to Final Cut」
- **核心理念**: 「The screenplay is the source of truth」
- **差异化**: 格式化与协作兼得 — 「Most screenwriting tools force a choice」
- **平台**: Web + macOS + Windows

### 4.2 核心功能

| 功能 | 能力 | 置信度 |
|------|------|--------|
| 格式化 | 自动 US/UK/French 标准排版 | 高 |
| 多语言 | CJK 排版 + 多语言场景头 | 高 |
| 结构化 DB | 自动提取场景/人物/地点 | 高 |
| 协作 | CRDT 实时多端同步 | 高 (已确认) |
| AI 架构 | Multi-agent 多元叙事视角 | 高 |
| 视觉生成 | 角色画像 + 分镜 Storyboard | 高 |
| 格式导出 | PDF / FDX / Fountain | 高 (已确认) |

### 4.3 定价体系

| 套餐 | 月费 | AI 对话 | 视觉 tokens | 项目 |
|------|------|--------|-----------|------|
| Junior | $0 | 15/月 | 10/日 | 2 |
| Senior | $20 | 500 | 600 | 3 |
| Master | $100 | 无限 | 6,000 | 无限 |

### 4.4 与 LumenX 对标

| 维度 | laper.ai | LumenX (现状) | LumenX 优势 |
|------|----------|-------------|------------|
| 格式引擎 | 自动 US/UK/FR+CJK | 无 (纯 textarea) | — |
| 结构化 DB | 自动提取+实时同步 | 手动触发 LLM | — |
| AI 深度 | Multi-agent+续写 | 仅实体提取 | — |
| 协作 | CRDT 实时 | 无 | — |
| **视频管线** | 无 | 完整管线 | **核心领先** |
| **API/集成** | 无 API 无自托管 | 完整 REST API | **显著优势** |
| **视觉生成** | 角色画像+分镜图 | 资产+分镜+**视频合成** | **维度更广** |

### 4.5 UX 设计亮点

1. 「剧本作为生产中心」范式 — 数据库与编辑器深度联动
2. 引擎分层定价 (16mm/35mm/IMAX) — 直观认知映射
3. 格式化为 AI 职责 — 作者专注创作
4. CJK 一键切换 — 好莱坞↔亚洲格式
5. 无需注册即可试用 — PLG 策略

---

## 五、业界需求与趋势

### 5.1 市场规模

| 来源 | 2025 | 2034 预测 | CAGR |
|------|------|---------|------|
| Fortune Business Insights | $220.11M | $1,012.63M | 18.48% |
| Straits Research | $186.21M | $860.20M | 17.4% |

北美占 39% 份额，**亚太增速最快 (19.2% CAGR)**，中国/印度驱动。

### 5.2 编剧 AI 使用现状 (ACM CHI 2025)

- **AI 使用率**: 78% 专业编剧
- **痛点**: 叙事连贯性差 96% | 创意卡壳 65% | 情感共鸣弱 35%
- **满意度**: 早期构思 100% → 结构化 16.7% → 起草 11% → 对话 0%
- **期望角色**: 演员(模拟角色) > 观众(评估) > 专家(批评) > 执行者(起草)

### 5.3 关键趋势

| 趋势 | 描述 | LumenX 行动 |
|------|------|-----------|
| AI-Native 编辑流 | AI 嵌入全流程 | 从按钮升级为持续性 co-author |
| Block-Based 编辑 | Notion/Craft 范式 | textarea → Tiptap block editor |
| 格式互操作 | Fountain/FDX 是连接件 | 优先 Fountain 导入导出 + FDX |
| AI as CRDT Peer | AI Agent 作文档对等节点 | 预留 Yjs 架构 |
| 持久故事记忆 | 跨场景连贯追踪 | 强化实体 DB 双向同步 |
| CJK 本土化 | 中文格式+短剧适配 | 中文场景头+短剧格式 |
| 剧本→视觉一体化 | 编剧→视频管线 | LumenX 已领先 |
| 亚太最快增长 | 19.2% CAGR | 抓住中文短剧×AI 视频交叉点 |

### 5.4 WGA AI 政策

AI 功能须设计为**可选、透明、可关闭**。禁止强制使用。

---

## 六、技术方向建议

### 6.1 编辑器框架选型

| 框架 | 定制性 | 协作 | 上手 | 推荐 |
|------|--------|------|------|------|
| ProseMirror | ★★★★★ | Yjs 原生 | 高 | 从零定制 |
| **Tiptap** | ★★★★ | 内置 Yjs | **低** | **推荐** |
| CodeMirror 6 | ★★★★ | 插件化 | 中 | Fountain 模式 |
| Slate | ★★★★★ | slate-yjs | 中 | 精细交互 |

**推荐方案**: Tiptap 主编辑器 + CodeMirror 6 Fountain 原始模式 + Fountain.js 预览渲染

### 6.2 三层解析架构

| 层 | 技术 | 职责 | 延迟 | 成本 |
|---|------|------|------|------|
| L1 | Fountain.js + 中文扩展 | 标准格式快速识别 | <1ms | 零 |
| L2 | DashScope/Qwen LLM | 中文特有格式识别 | ~1s | 中 |
| L3 | Block editor 手动切换 | 用户最终裁定 | 即时 | 零 |

**原则**: 规则优先 → LLM 兜底 → 用户做主

### 6.3 目标数据模型

```typescript
interface ScriptDocument {
  blocks: ScriptBlock[];
  meta: { title: string; author: string; version: number; };
}

interface ScriptBlock {
  id: string;
  type: 'scene_heading' | 'action' | 'character' | 'dialogue' 
      | 'parenthetical' | 'transition' | 'narration' | 'note' | 'section';
  text: string;
  annotations?: {
    characterId?: string;
    sceneId?: string;
    confidence?: number;
  };
}
```

### 6.4 协作架构 (预留)

- **CRDT**: Yjs (Tiptap 内置绑定)
- **AI as Peer**: AI Agent 作为 Yjs awareness 节点参与编辑
- **传输**: WebSocket (HocusPocus 服务端 / 自建)

---

## 七、前端设计方向建议

### 7.1 设计理念

从「空白 textarea」升级为「沉浸式创作座舱」:
- **Block 编辑**: 每个剧本元素是独立 Block，可折叠、拖拽、AI 标注
- **三栏布局**: 大纲面板 (左) | 编辑区 (中) | 属性/AI 面板 (右)
- **格式即视觉**: 场景头、对白、动作各有独特样式，所见即所得
- **AI 气泡**: 续写/改写建议以非侵入式气泡呈现

### 7.2 视觉语言

| 维度 | 方向 |
|------|------|
| 色彩 | 深色主题延续 (#050508 背景)，剧本元素用语义色区分 |
| 字体 | 编辑区: JetBrains Mono (等宽, 格式对齐); 界面: Space Grotesk |
| 间距 | 模拟剧本物理格式 — 角色名居中、对白缩进、场景头全宽 |
| 动效 | Block 折叠展开、AI 建议淡入、元素类型切换过渡 |
| 签名元素 | 左侧场景色带 — 通过颜色直觉标识场景情绪 |

### 7.3 关键交互模式

1. **`/` 命令面板**: 快速切换 Block 类型 + 调用 AI
2. **侧边大纲**: 场景卡片可拖拽重排
3. **Inline AI**: 选中文本 → 悬浮工具栏 → 续写/改写/评估
4. **分屏预览**: 左侧编辑 | 右侧格式化预览 (模拟打印效果)
5. **实体面板**: 自动识别角色/地点 → 右侧面板实时展示关联

---

## 八、功能路线图建议

### Phase 1: 格式引擎 (P0, 4–6 周)

- [ ] 引入 Tiptap 替代 textarea
- [ ] 实现 Fountain Block 类型 (scene_heading/action/character/dialogue 等)
- [ ] 中文场景头扩展 (内景/外景)
- [ ] 语法高亮 + 行号
- [ ] 基础大纲导航面板

### Phase 2: 结构化 + AI (P1, 6–8 周)

- [ ] 实体 DB 实时同步 (Block 变更 → 自动更新角色/场景库)
- [ ] Inline AI 续写/改写 (DashScope Qwen)
- [ ] `/` 命令面板 (Block 类型切换 + AI 调用)
- [ ] Fountain/FDX/PDF 导出
- [ ] 统计面板 (字数/页数/时长/角色对白占比)

### Phase 3: 协作 + 高级 (P2, 8–12 周)

- [ ] Yjs 实时协作 (WebSocket 传输)
- [ ] 版本历史 + 变更追踪
- [ ] AI as CRDT Peer (自动建议 + 格式修正)
- [ ] 分屏格式化预览
- [ ] 角色弧光追踪面板

### Phase 4: 生态 (P3, 12+ 周)

- [ ] CJK 多语言排版切换
- [ ] 短剧模板 (竖屏分镜 + 简化格式)
- [ ] 插件系统 (自定义 Block 类型)
- [ ] 第三方集成 (Final Draft 导入/导出完整性)
- [ ] 多项目管理 + 模板库

---

## 九、参考来源

| # | 来源 | 类型 | 置信度 |
|---|------|------|--------|
| S1 | Fortune Business Insights 市场报告 (2025) | 市场研究 | 高 |
| S2 | fountain.io 官方规范 | 直接 | 高 |
| S3 | mattdaly/Fountain.js GitHub | 直接 | 高 |
| S4 | wildwinter/screenplay-tools GitHub | 直接 | 高 |
| S5 | piersdeseilligny/betterfountain GitHub | 直接 | 高 |
| S8 | trelby.org | 直接 | 高 |
| S10 | arcstudiopro.com | 直接 | 高 |
| S11 | writerduet.com | 直接 | 高 |
| S14 | finaldraft.com | 直接 | 高 |
| S15 | laper.ai 首页 (历史提取) | 直接 | 中-高 |
| S21 | arXiv 2502.16153v1 (ACM CHI 2025) | 学术 | 高 |
| S22 | wga.org AI 政策页 | 直接 | 高 |
| S23 | storyflow.so 2026 对比 | 直接 | 中-高 |
| S25 | moyancn.com (有戏 XScript Pro) | 直接 | 高 |
| S26 | CSDN 工具合集 | 直接 | 中 |
| S27 | Liveblocks 富文本框架对比 | 直接 | 高 |
| S28 | tiptap.dev 官方文档 | 直接 | 高 |
| S32 | tron-comic 代码库 (ScriptProcessor.tsx) | 直接 | 极高 |
| S34 | Straits Research 市场报告 (2026) | 市场研究 | 高 |
| S37 | finalbitai.com/blog | 直接 | 高 |
| S39 | 火山引擎全维度评测 (2026) | 直接 | 高 |
| S40 | aidiveforge.com/listing/laper/ | 直接 | 高 |

---

*报告终。基于 40+ 信息来源的综合分析，为 LumenX Studio 剧本编辑器重塑提供技术、设计与功能方向参考。*
