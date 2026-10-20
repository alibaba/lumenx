# 剧本编辑器多源调研原始素材笔记（v5.0）

> **项目**：LumenX Studio（tron-comic）  
> **任务**：task_yy3jyl3z56 — 多源调研：开源工具、竞品、业界趋势与参考资料内容采集  
> **日期**：2026-06-25  
> **版本**：v5.0（综合 v4.0 素材 + 2026-06-25 本轮新增数据）  
> **用途**：供 T2 综合分析、路线图撰写及 HTML 展示使用  
> **说明**：每条结论均标注信息来源与置信度（高 / 中 / 低）。直接采集标注「直接来源」，推断或二手标注「间接来源」。

---

## 一、调研范围与参考来源状态

### 1.1 用户提供的参考链接采集状态

| # | 链接 | 状态 | 处理方式 | 结果 / 原因 |
|---|------|------|----------|-------------|
| 1 | https://mp.weixin.qq.com/s/d-lSbaj9mxd4oA3ModO-Bw | **访问被拦截** | WebFetch 多次尝试 | 微信安全验证拦截页（「环境异常，完成验证后即可继续访问」）；r.jina.ai IP 阻断 |
| 2 | 小红书笔记 ① | **链接未提供** | 待补充 | 任务描述要求小红书笔记，但 mission 中未给出完整 URL |
| 3 | 小红书笔记 ② | **链接未提供** | 待补充 | 同上 |

**处理说明**：
- 微信文章标题含「中国软件爆杀…」，结合中文市场调研已用 moyancn.com、CSDN 等国内来源补充国产剧本编辑器信息。
- 小红书笔记缺失会限制「真实用户反馈/本土使用习惯」维度素材。
- laper.ai 官网受 JS 渲染保护，正文无法提取；以历史提取结果 + 博客索引信息为基础。

### 1.2 公开来源覆盖范围

| 来源类别 | 覆盖情况 | 本轮新增 |
|---------|---------|---------|
| Fountain 官方规范与解析库 | ✅ fountain.io + GitHub | — |
| 开源实现（Fountain.js / screenplay-tools / BetterFountain / Trelby） | ✅ 高置信度 | — |
| 商业工具（Arc Studio / WriterDuet / Highland 2 / Fade In / Celtx / Final Draft） | ✅ 中-高置信度 | ✅ storyflow.so 完整对比数据 |
| 竞品（laper.ai / Melies / NolanAI / Storyflow / Scriptmatix / FinalBit） | ✅ 中-高置信度 | ✅ FinalBit 2026 新竞品数据 |
| 中文市场工具（有戏 XScript Pro / 写作大师 / 故事工厂 / 字画） | ✅ 中-高置信度 | — |
| 行业数据（市场规模、AI 使用率、编剧访谈研究） | ✅ 高置信度 | ✅ Straits Research 第二来源验证 |
| 技术趋势（CRDT+AI / Tiptap 扩展 / Yjs） | ✅ 中-高置信度 | ✅ Electric.ax CRDT-AI 架构 |
| LumenX 项目代码与文档 | ✅ 极高置信度 | ✅ 完整代码复核 |

---

## 二、微信公众号文章内容（访问失败，上下文推断）

> **来源**：https://mp.weixin.qq.com/s/d-lSbaj9mxd4oA3ModO-Bw  
> **置信度**：N/A（正文不可访问）；主题推断为 **中**

### 2.1 访问尝试记录

- **WebFetch**（2026-06-25）：返回「环境异常，完成验证后即可继续访问」安全拦截页。
- **r.jina.ai**（历史尝试）：返回 IP 声誉阻断。
- **browser-harness**：需要 Chrome DevTools 远程调试，当前环境未启用。

### 2.2 标题与主题推断

标题含「中国软件爆杀…」，结合 2025–2026 年国产创作工具发展趋势，文章可能涉及：
1. 国产剧本编辑/AI 剧本工具在功能、价格或本土化方面与国际竞品的对比；
2. 有戏 XScript Pro、字画、故事工厂等中文工具的突破性能力展示；
3. 编剧/创作者视角下的「国产替代」经验分享。

### 2.3 数据缺口

- 正文、截图、具体产品名与数据点均缺失。
- **建议**：若文章观点至关重要，可请用户手动复制正文或开启 Chrome 远程调试后由 browser-harness 重新抓取。

---

## 三、小红书笔记摘要（链接缺失）

> **来源**：小红书笔记 ①/②（链接未提供）  
> **置信度**：N/A

**说明**：任务描述要求处理两篇小红书笔记并使用 browser-harness，但 mission 文本中未提供完整 URL。缺少社交媒体用户真实痛点与本土工具使用反馈。建议用户补充 URL。

---

## 四、主流开源剧本编辑器技术实现与能力对比

### 4.1 Fountain 生态（直接来源，置信度：高）

**来源**：fountain.io 官方文档、mattdaly/Fountain.js GitHub、wildwinter/screenplay-tools GitHub

Fountain 是纯文本剧本格式标准（2012 年发起），设计哲学是「任何文本编辑器、任何设备都能写剧本」。官方定位于**创作早期阶段**，不含制片阶段的锁定页、彩色修订等功能。

#### 4.1.1 核心语法元素

| 元素 | 语法规则 | 示例（中文适配） |
|------|---------|--------------|
| 场景头（Scene Heading） | `INT.`/`EXT.` 前缀或强制 `.` | `INT. 咖啡馆 - 夜` |
| 动作/场景描述（Action） | 普通段落 | 李明走进咖啡馆，灯光昏暗。 |
| 角色名（Character） | 全大写 + 上方空行 | `李明` |
| 括号提示（Parenthetical） | `()` | `(低声)` |
| 对白（Dialogue） | 角色名下方 | 我不知道该怎么说。 |
| 转场（Transition） | `>` 或全大写 | `切至：` / `CUT TO:` |
| 章节（Section） | `#` 多级 | `## 第一幕` |
| 大纲摘要（Synopsis） | `= 摘要文本` | `= 李明到达咖啡馆` |
| 注释（Notes） | `[[]]` | `[[这里需要修改]]` |
| 废弃段落（Boneyard） | `/**/` 包裹 | `/* 废弃段落 */` |
| 分页符（Page Break） | `===` | |
| 标题页（Title Page） | 键值对 + 空行 | `Title: 剧本名` |

#### 4.1.2 中文适配挑战

- 中文剧本常用「内景」/「外景」，与 `INT.`/`EXT.` 不兼容
- 对白识别依赖全大写角色名，中文无大小写之分
- **建议方案**：扩展 Fountain 解析规则支持中文前缀（`内景.`/`外景.`），并通过 LLM 兜底识别非标准段落

#### 4.1.3 Fountain.js 技术细节

| 特性 | 内容 |
|------|------|
| 核心 API | `fountain.parse(scriptString, callback)` |
| 输出格式 | `{ title, html: { title_page, script } }`；可选 `tokens` 数组 |
| Token 类型 | `scene_heading`, `action`, `character`, `dialogue`, `parenthetical`, `transition`, `section`, `synopsis`, `note`, `boneyard`, `page_break` |
| 复合元素 | 双对话通过 `dual_dialogue_begin`/`dual_dialogue_end` wrapper |
| 规范版本 | 完整实现 Fountain v0.1.8 |
| 兼容性 | 浏览器 + Node.js 双环境 |

**置信度**：高（GitHub 直接读取）

### 4.2 开源实现能力对比矩阵

| 工具 | 语言/形态 | 核心能力 | FDX 互通 | 许可证 | LumenX 借鉴价值 | 置信度 |
|------|----------|---------|----------|--------|----------------|--------|
| **Fountain.js** | JavaScript | 浏览器/Node 解析；HTML + tokens AST 输出 | 否 | 未明确 | ★★★ 前端直接集成 | 高 |
| **screenplay-tools** | C++/JS/Python/C# | 跨语言数据模型；Fountain↔FDX 双向转换 | 是 | 未明确 | ★★★ 后端数据模型参考 | 高 |
| **BetterFountain** | VSCode 扩展 | 语法高亮+智能补全+大纲视图+实时排版预览+统计面板+时长估算+PDF 导出 | 否 | MIT | ★★★ 编辑器交互设计金标准 | 高 |
| **Afterwriting** | Web (开源) | 在线编辑+PDF+云同步+对话/节奏/场景时长分析 | 输入 FDX | 开源 | ★★ 在线编辑+分析面板 | 中 |
| **Trelby** | Python 桌面 | 强制格式规范；分页控制；20 万角色名库；版本对比 | 是 | GPL | ★★ 完整桌面应用参考 | 高 |
| **KIT Scenarist** | 跨平台 | 卡片看板；AI 助手；海报生成；全格式导入导出 | 是 | 免费 | ★★ 卡片式大纲+多格式 | 中 |
| **nyousefi/Fountain** | Objective-C | 官方参考实现；FastFountainParser 10× 加速 | 否 | MIT | ★ 解析器架构参考 | 高 |

### 4.3 商业工具能力矩阵（含 2026 最新数据）

> **来源**：storyflow.so「Best Final Draft Alternatives 2026」+ worldmetrics.org Top 10 2026 + 官网信息  
> **置信度**：高

| 工具 | 定位 | 核心能力亮点 | 协作 | 定价 | AI | 评分/10 |
|------|------|------------|------|------|-----|---------|
| **Final Draft 13** | 行业标准（30年） | 格式合规性最高；3M+ 用户；95% 好莱坞使用 | 有限 | $199.99 一次性 | 最小 | 9.5 |
| **WriterDuet** | 云端协作核心 | 实时光标追踪+修订追踪+故事卡片+多角色语音朗读 | 多人实时 | $11.99/月 或 $99/年 | 生成式（脚本语法训练） | 9.1 |
| **Arc Studio Pro** | 云端现代编辑器 | 离线+自动排版+数字画布 beat board+实时协作 | 多人实时 | $69–$99/年 | 基础 beat 辅助 | — |
| **Celtx** | 制片管理+剧本 | 预算排期+前期统筹+模板库 | 有 | $15/用户/月 | 场景提示 | 8.8 |
| **Fade In** | 跨平台专业 | 多语言+script locking+修订追踪+大纲导航 | 无 | $79.95 一次性 | 无 | 6.9 |
| **Highland 2** | macOS 极简写作 | 极简起草+专注模式+角色性别分析+里程碑追踪 | 无 | $49.99 一次性 | AI 大纲→初稿 | 7.9 |
| **Storyflow** | 结构化规划 | 全画布上下文+Save the Cat 框架+视觉规划 | — | $7.99/月 | 叙事框架评估 | — |
| **Slugline** | macOS 纯文本 | Fountain 原生+极简+专注模式 | 无 | $39.99 一次性 | 无 | — |
| **Beat** | Mac 免费 | 免费+Fountain 原生+Mac App | 无 | 免费 | 无 | — |

**2026 年商业工具关键洞察**（来源：storyflow.so [S23]）：

1. **格式标准依然是核心**：排版合规性权重 25%，领先于协作（20%）和 AI（未单独权重）
2. **协作是分水岭**：仅 WriterDuet、Celtx、Arc Studio 实现真正实时光标同步
3. **AI 功能仍处于初级**：大部分工具的 AI 仅限基础建议，WriterDuet 最先进但仍非「AI-native」
4. **一次性 vs 订阅**：Fade In ($79.95) 和 Highland 2 ($49.99) 是一次性购买的最优解
5. **63% 编剧愿意迁移**：只要云端工具支持可靠 FDX 导出

### 4.4 开源生态关键结论

1. **Fountain 是低成本高价值起点**：引入 Fountain.js 可零成本实现基础结构识别
2. **实现分层清晰**：底层解析器 → IDE 扩展 → 在线编辑器+PDF → 完整桌面应用。LumenX 当前处于「底层以下」
3. **BetterFountain 是编辑器交互金标准**：实时排版预览+大纲视图+统计面板+时长估算+点击跳转
4. **Arc Studio Pro 代表云端现代形态**：云端+本地缓存双写+自动排版+规划套件+实时协作
5. **Highland 2 是 Fountain-native 极简的标杆**：证明纯文本标记+极简 UI 可以覆盖独立创作者需求

---

## 五、竞品 laper.ai 深度分析

> **置信度说明**：官网正文受 JS 渲染保护，本轮 WebFetch 仅返回 CSS。以下为 2026-06-22 历史提取结果 + 博客索引信息的综合。

### 5.1 产品定位与核心理念

- **Slogan**：「From First Draft to Final Cut」
- **核心价值主张**：「The screenplay is the source of truth」—— 剧本是生产全链路的单一数据中心
- **目标用户**：全谱系编剧（初学者→资深从业者→协同团队）
- **市场定位**：全流程 AI 原生剧本创作平台
- **内容策略**：大量 SEO 博客（「AI Screenwriting Tools Comparison 2026」「Best Screenwriting Software 2026」等）

**置信度**：高（r.jina.ai 历史提取官网核心文案）

### 5.2 核心功能清单

| 功能类别 | 具体能力 | 置信度 |
|---------|---------|--------|
| **格式化** | 自动好莱坞行业标准排版；多语言 | 高 |
| **多语言** | 跨多种语言生成场景头/角色提示/对白；CJK 排版支持 | 高 |
| **结构化数据库** | 自动从剧本提取场景/人物/地点结构化数据库 | 高 |
| **协作** | CRDT-based 实时多端同步编辑 | 中（推断） |
| **视觉生成** | AI 生成角色画像（character portraits） | 高 |
| **分镜生成** | 从剧本生成 shot/storyboard 图像 | 高 |
| **商业提案** | Pitch deck 辅助生成 | 中 |
| **节拍表** | Beat sheet 结构辅助 | 中 |
| **角色连贯性** | 跨场景角色特征与弧光自动追踪 | 中 |
| **创作辅助** | 构思/大纲/对话润色/续写；序列式分段生成 | 中 |
| **格式导出** | PDF / DOCX / TXT / FDX；好莱坞与亚洲 CJK 排版切换 | 高 |

### 5.3 定价体系

| 套餐 | 月费 | AI 对话数 | 视觉 tokens | 项目槽位 | 关键特权 |
|------|------|----------|------------|---------|---------|
| **Junior** | $0 | 15/月 | 10/日 | 2 | 标准编辑工具 |
| **Senior** | $20 | 500 | 600 | 3 | Laper 16mm 引擎 |
| **Elite** | $60 | 2,000 | 2,800 | 20 | Laper 35mm 模型 |
| **Master** | $100 | 无限 | 6,000 | 无限 | Laper IMAX；团队编辑 |
| **Legend** | $400 | 无限 | 32,000 | 无限 | 专属通道；1-on-1 支持 |

### 5.4 UX 设计亮点

1. **「剧本作为生产中心」交互范式**：数据库与编辑器深度联动
2. **引擎分层定价**：16mm / 35mm / IMAX 映射生成质量，直观认知
3. **AI 任务队列模式**：积分返还机制（fairness），降低试错成本
4. **CJK 排版一键切换**：好莱坞 ↔ 亚洲格式
5. **无需注册即可试用**：PLG 策略

### 5.5 与 LumenX 的维度对比

| 维度 | laper.ai | LumenX（现状） | 差距 | 优先级 |
|------|----------|--------------|------|--------|
| 剧本格式化引擎 | 自动行业标准排版 | 无（纯 textarea） | 严重 | P0 |
| 结构化数据库 | 自动提取场景/人物/地点 | 手动触发 LLM 提取 | 大 | P1 |
| AI 嵌入深度 | 全流程（格式化/续写/诊断/视觉） | 仅「提取实体」按钮 | 严重 | P1 |
| 协作模式 | CRDT 实时多人 | 无 | 中 | P2 |
| 视觉生成 | 角色画像 + 分镜图 | 资产+分镜+**视频合成** | **LumenX 领先** | 差异化 |
| 格式导出 | PDF/DOCX/TXT/FDX + CJK | 无 | 严重 | P1 |
| 视频生成管线 | 无 | 完整（分镜→资产→视频→合成） | **LumenX 核心优势** | — |
| 系列感知 | 未知 | ReconcileModal 跨集资产合并 | **LumenX 独特** | — |

### 5.6 新竞品：FinalBit AI（2026 新入场）

> **来源**：finalbitai.com/blog（WebFetch 直接提取）  
> **置信度**：高

| 维度 | FinalBit AI |
|------|-------------|
| **核心差异化** | 「Persistent Story Memory」— 持久故事记忆，主动监控角色弧线与情节连贯性 |
| **AI 架构** | 专门训练的剧本模型（非通用 LLM），理解叙事框架 |
| **格式输出** | 自动生成 Final Draft FDX + Fountain 兼容输出 |
| **协作模式** | Suggestion-and-accept 模型；团队共享叙事元素数据库 |
| **附加能力** | Treatment 摘要、营销 pitch、logline 生成；路线图含语音朗读+开发分析 |
| **定价** | Mid-to-upper range 订阅制 |
| **对 LumenX 启示** | 「故事记忆」概念可用于强化跨场景连贯性追踪 |

### 5.7 竞品生态全景（更新版）

| 工具 | 定位 | 核心 AI 能力 | 差异化 |
|------|------|------------|--------|
| **laper.ai** | 全流程 AI 原生 | 序列生成+超大上下文+CJK | 剧本→视觉生成闭环 |
| **FinalBit AI** | 结构感知型 | 持久故事记忆+叙事框架理解 | 角色/情节连贯性追踪 |
| **NolanAI** | 完整制片环境 | 自动脚本分拆+安全加密 | 专业制片安全级别 |
| **Melies** | 多媒体叙事 | 音频/视觉/剪辑集成；10M+ tokens | 超大上下文+多媒体 |
| **Scriptmatix** | AI 故事引擎 | Beat-by-beat+角色弧光建模 | 结构逻辑最深 |
| **Storyflow** | 结构化规划 | 全画布+Save the Cat 框架 | 叙事结构框架 |
| **Sudowrite** | AI 小说/散文 | Story Engine 3.0；1000+ 插件 | 生成深度（无剧本格式） |
| **Arc Studio Pro** | 现代格式+协作 | beat board+实时协作 | 传统格式×协作 |

---

## 六、业界需求与趋势

### 6.1 市场规模与增长（双来源交叉验证）

| 来源 | 2025 年规模 | 2034 年预测 | CAGR | 置信度 |
|------|-----------|-----------|------|--------|
| Fortune Business Insights [S1] | $220.11M | $1,012.63M | 18.48% | 高 |
| Straits Research [S34] | $186.21M | $860.20M | 17.4% | 高 |

**综合结论**：两家机构口径差异约 15%（可能因包含范围不同），但均确认市场处于 17-19% 高速增长区间。北美占 39% 市场份额，**亚太增速最快（19.2% CAGR）**，主要由中国和印度的数字内容爆发驱动。

**市场驱动因素**：
- 全球流媒体内容生产扩张
- 创作者经济规模化
- AI 工具成本急速下降
- 云端协作需求成为基础设施级

### 6.2 编剧 AI 使用现状（学术来源）

> **来源**：arXiv 2502.16153v1 — ACM CHI 2025；23 位专业编剧深度访谈  
> **置信度**：高

**AI 使用率**：78%（18/23）活跃使用

**四大核心痛点**：

| 痛点 | 受影响人数 | 描述 |
|------|----------|------|
| 叙事连贯性差 | 22/23 (96%) | 跨场景角色与情节一致性最普遍问题 |
| 创意卡壳 | 15/23 (65%) | 早期构思阶段高发 |
| 情感共鸣弱 | 8/23 (35%) | 对话与角色塑造缺乏真实感 |
| 主题深度不足 | 7/23 (30%) | AI 生成内容流于表面 |

**AI 满意度（分阶段）**：

| 创作阶段 | 满意度 | 关键洞察 |
|---------|--------|---------|
| 早期构思 | **100%** | AI 在发散创意时价值最高 |
| 故事结构化 | **16.7%** | 结构化引导能力严重不足 |
| 完整剧本起草 | **11%** | 长文本连贯性差 |
| 对话生成 | **0%** | AI 工具最弱环节 |

**编剧期望的四种 AI 协作角色**：

| 角色 | 期望能力 | LumenX 映射 |
|------|---------|------------|
| 演员（Actor） | 模拟角色心理与行为反馈 | → 角色档案+对话预览 |
| 观众（Audience） | 受众接受度评估 | → 分镜风格预览 |
| 专家（Expert） | 叙事节奏批评+创新管线 | → 结构诊断面板 |
| 执行者（Executor） | 繁琐起草+可视化+追踪弧线 | → 实体提取+分镜生成 |

### 6.3 格式标准需求

**互操作格式优先级**：

| 格式 | 适用阶段 | 特点 | 优先级 |
|------|---------|------|--------|
| **Fountain** | 初稿/存档 | 纯文本、开源、可读性强 | P0 |
| **FDX** | 行业交付/生产 | 工业互操作标准 | P1 |
| **PDF** | 审阅/投递 | 无损展示 | P1 |
| **DOCX** | 通用文档 | 通用性强 | P2 |

**关键数据**：63% 编剧愿意迁移到支持可靠 FDX 导出的云端工具（storyflow.so [S23]）

### 6.4 协作需求

| 需求 | 行业现状 | LumenX |
|------|---------|--------|
| 实时光标追踪 | 仅 WriterDuet / Celtx / Arc Studio | 无 |
| 变更追踪/修订归因 | WriterDuet 最完整 | 无 |
| 角色权限分级 | 基于角色的差异化编辑权限 | 无 |
| CRDT 冲突解决 | 云端编辑最优解 | 无 |
| 评论/批注 | 标注与回复链 | 无 |

**2026 趋势更新**：AI Agents 作为 CRDT Peers 的架构正在出现——Electric.ax（2026-04）提出让 AI agent 作为 Yjs document 的对等节点参与协作编辑，意味着 AI 可以像人类协作者一样加入文档编辑。这对 LumenX 的长期 AI 协作架构有重要参考意义。

### 6.5 WGA 对 AI 的政策立场

| 条款 | 内容 |
|------|------|
| 禁止 | 强制编剧使用 AI；将 AI 内容认定为文学材料 |
| 允许 | 编剧自愿使用（须公司同意） |
| 披露义务 | 制片公司须告知编剧草稿是否含 AI 内容 |
| 训练保护 | 用编剧作品训练 AI 违反协议 |

**产品设计影响**：AI 功能须设计为**可选、透明、可关闭**的辅助工具。

### 6.6 关键趋势总结

| 趋势 | 描述 | 对 LumenX 行动建议 |
|------|------|-------------------|
| AI-Native 编辑流 | AI 嵌入全流程，非孤立按钮 | 从「提取实体按钮」升级为持续性 AI co-author |
| Block-Based 结构化 | Notion/Craft 范式成为标准 | 从 textarea 迁移至 block editor（Tiptap） |
| 开放格式互操作 | Fountain/FDX 是专业工具链连接件 | 优先支持 Fountain 导入导出 + FDX 导出 |
| AI as CRDT Peer | AI Agent 作为文档编辑的对等参与者 | 预留 Yjs 架构使 AI 可作为协作节点 |
| 持久故事记忆 | 跨场景角色/情节连贯性追踪 | 强化实体数据库与剧本的实时双向同步 |
| CJK 本土化 | 中文格式引擎、短剧/短视频适配 | 中文场景头规则、短剧格式支持 |
| 剧本→视觉一体化 | 编剧→画像→分镜→视频完整管线 | LumenX 已领先，需强化剧本驱动后端 |
| 亚太增速最快 | 19.2% CAGR，中国/印度驱动 | 抓住中文短剧 + AI 视频生成的交叉点 |

---

## 七、中文市场专题调研

### 7.1 国内主要工具

| 工具 | 核心特色 | 技术亮点 | 对 LumenX 启示 |
|------|---------|---------|----------------|
| **有戏/来戏 XScript Pro** | 下一代中文剧本创作 | 12 种段落布局；卷/季/集/幕/场五级层级；9 种格式导入；10 种导出（PDF/HTML）；好莱坞/传统/微信聊天格式切换 | 中文优先+短视频格式+多层级结构 |
| **写作大师** | 格式模板+团队协作 | 叙事弧线；角色档案；团队实时编辑 | 角色档案与叙事弧线管理 |
| **故事工厂** | 全平台云协作 | 剧情大纲+角色设定+场景设计+多用户实时编辑 | 面向电影/短剧/动漫 |
| **字画（字节跳动）** | 剧本创作+脑图 | 叙事分支+强云同步；字节生态 | 字节生态集成+分支叙事 |

### 7.2 有戏 XScript Pro 深度解析

- **零门槛入口**：「无需掌握任何剧本知识，即可开始创作」
- **多格式兼容**：可导入 Celtx / Final Draft / Word / Fade In 等 9 种格式
- **格式灵活切换**：传统中文 / 好莱坞格式 / **微信聊天格式**（面向短视频/网剧）
- **层级组织**：卷→季→集→幕→场
- **视觉化写作**：12 种行业段落结构+思维导图工作区
- **发行通道**：直接投递剧本给影视公司/数字平台

**核心启示**：「微信聊天格式」说明中国短视频市场存在非传统剧本格式的真实需求。

### 7.3 中文市场关键结论

1. 国产工具已完成本土化，不再是「Final Draft 的中文翻版」
2. 短剧是增量市场——格式简化、产能更快、竖屏分镜
3. LumenX 的「剧本→分镜→视频」AI 生成管线是国产工具完全缺失的能力

---

## 八、LumenX 当前剧本编辑器现状（代码直接检查）

> **来源**：代码库直接读取  
> **置信度**：极高

### 8.1 前端实现

**文件路径**：`frontend/src/components/modules/ScriptProcessor.tsx`

| 维度 | 现状 | 评级 |
|------|------|------|
| 编辑形态 | 纯 `<textarea>` 编辑 `originalText` 字符串 | 无结构化 |
| 持久化策略 | `onChange` → Zustand；`onBlur` → `PUT /projects/{id}/text` | 两层保存合理 |
| 实体提取 | 点击「提取实体」→ LLM → EntityConfirmModal 确认 | 交互割裂 |
| 格式感知 | 无 | — |
| 语法高亮 | 无 | — |
| Fountain 支持 | 无 | — |
| 大纲导航 | 无 | — |
| 实时预览 | 无 | — |
| 协作 | 无 | — |
| 统计面板 | 无 | — |

**关键代码路径**：
```
用户输入 textarea
  → onChange: setScript(local) + updateProject(Zustand)
  → onBlur: api.updateScriptText(id, script) → PUT /projects/{id}/text
  → 点击「提取实体」: api.extractPreview(id, script) → POST /projects/{id}/extract_preview → LLM
  → EntityConfirmModal 确认: api.reparseProject(id, script) → PUT /projects/{id}/reparse
```

### 8.2 后端实现

**核心文件**：`src/apps/comic_gen/llm.py`（ScriptProcessor 类）

| 能力 | 现状 |
|------|------|
| 实体提取 | `parse_novel(title, text)` — LLM 完整提取 |
| 分帧生成 | `analyze_to_storyboard()` — LLM 从文本+实体生成 StoryboardFrame[] |
| 规则解析层 | **无** — 完全依赖 LLM |
| Fountain/FDX 支持 | **无** |
| 数据模型 | `original_text: str`（纯字符串） |

**后端已有格式约定**（LLM Prompt 中内置）：
- 场景标题行: `1-1 地点名称 [时间] [内/外]`
- 人物行: `人物：角色名1，角色名2`
- 动作描述: 以 `△` 开头
- 对白: `角色名（情绪）：对话内容`

### 8.3 核心缺口诊断矩阵

| 功能维度 | 行业标准 | LumenX 现状 | 差距 | 优先级 |
|---------|---------|------------|------|--------|
| 格式引擎 | 自动场景头/对话/括注 | 无 | **严重** | P0 |
| 语法高亮 | 元素视觉区分 | 无 | **严重** | P0 |
| Fountain 解析 | 纯文本标记+结构识别 | 无 | **严重** | P0 |
| 格式导出 | FDX / PDF / Fountain | 无 | **严重** | P1 |
| 内联 AI 辅助 | 上下文感知续写/建议 | 无 | 重要 | P1 |
| 结构化数据库 | 实体与剧本实时同步 | 手动触发 | 重要 | P1 |
| 版本历史 | 完整变更追踪 | 无 | 中等 | P2 |
| 协作编辑 | 实时多人 | 无 | 中等 | P3 |

### 8.4 已有优势（差异化资产）

1. **两阶段实体提取**（extractPreview → confirm → reparseProject）
2. **系列感知**（ReconcileModal 跨集实体合并+前情提要）
3. **完整视频管线**（剧本→分镜→资产→视频合成）
4. **两层保存策略**（Zustand + blur 持久化）
5. **已有 Block 化规划**（`docs/plans/2026-03-16-structured-script-editor.md`）

---

## 九、技术与前端方向

### 9.1 富文本编辑器框架选型

| 框架 | 定制性 | 协作能力 | 上手难度 | 推荐场景 |
|------|--------|---------|---------|---------|
| **ProseMirror** | ★★★★★ | Yjs 原生 | 高 | 从零构建高度定制 |
| **Tiptap** | ★★★★ | 内置 Yjs + Liveblocks | **低** | **★ LumenX 推荐** |
| **CodeMirror 6** | ★★★★ | 插件化 | 中 | Fountain 纯文本高亮模式 |
| **Slate** | ★★★★★ | 需 slate-yjs | 中 | 精细交互 |

**推荐方案**：
- **主编辑器**：Tiptap（ProseMirror 封装，内置 Yjs，Headless UI 适配暗色玻璃态）
- **Fountain 模式**：CodeMirror 6 + 自定义 Fountain 语法插件
- **预览层**：Fountain.js 解析输出 HTML

**Tiptap 剧本扩展技术要点**（来源：tiptap.dev/docs [S35]）：
- Custom Node API：可定义 `SceneHeading`, `Character`, `Dialogue`, `Action` 等自定义节点类型
- Extension-collaboration：`@tiptap/extension-collaboration` 提供即插即用的 Yjs 协作支持
- Headless 模式：不绑定 UI 框架，可完全自定义渲染（适配 LumenX 暗色玻璃态设计）

### 9.2 三层解析架构

| 层级 | 技术 | 职责 | 延迟 | 成本 |
|------|------|------|------|------|
| **第一层：规则解析** | Fountain.js + 中文扩展规则 | 快速识别标准格式元素 | <1ms | 零 |
| **第二层：LLM 辅助** | DashScope/Qwen | 识别中文特有格式 | ~1s | 中 |
| **第三层：用户修正** | Block editor 手动切换类型 | 最终归属权交给用户 | 即时 | 零 |

**策略原则**：规则优先，LLM 兜底，用户做主。

### 9.3 数据模型升级方案

```typescript
// 目标状态
interface ScriptDocument {
  blocks: ScriptBlock[];
  meta: {
    title: string;
    author: string;
    fountainSource?: string;
    version: number;
    createdAt: string;
    updatedAt: string;
  };
}

interface ScriptBlock {
  id: string;
  type: 
    | 'scene_heading'
    | 'action'
    | 'character'
    | 'dialogue'
    | 'parenthetical'
    | 'transition'
    | 'narration'      // 中文特有
    | 'note'
    | 'section';
  text: string;
  annotations?: {
    characterId?: string;
    sceneId?: string;
    confidence?: number;
    manualOverride?: boolean;
  };
}
```

### 9.4 AI 嵌入点设计

| 触发点 | AI 功能 | 方式 |
|--------|---------|------|
| 场景头识别后 | 场景内容联想建议 | 侧边气泡 |
| 角色首次出现 | 自动关联/创建角色档案 | 内联高亮+快捷确认 |
| 对白块选中 | 情感/节奏建议 | 悬浮工具栏 |
| 剧本完成后 | 结构诊断报告 | 侧边分析面板 |
| 全局入口 | 续写/改写/扩展 | 命令面板（`/` 触发） |

### 9.5 AI as CRDT Peer 架构前瞻（新增）

> **来源**：Electric.ax blog 2026-04-08 [S36]  
> **置信度**：中（架构概念验证阶段）

2026 年出现的新模式是将 AI Agent 视为 Yjs document 的对等 CRDT 节点：
- AI 可以像人类协作者一样加入文档编辑
- 文档同步通过 Yjs awareness protocol 完成
- AI 的编辑可以被 undo/redo/reject
- 适合「AI 建议 → 人类审批」的工作流

**对 LumenX 的启示**：未来的 AI 辅助可以不再是「单独按钮触发」，而是作为编辑器中的一个协作者角色（如「AI 编剧助手」光标），实时提供建议并可随时撤回。

---

## 十、来源列表

| # | 来源 | 类型 | 置信度 |
|---|------|------|--------|
| [S1] | Fortune Business Insights, Screen & Script Writing Software Market (2025) | 市场研究 | 高 |
| [S2] | fountain.io 官方规范文档 | 直接来源 | 高 |
| [S3] | mattdaly/Fountain.js GitHub | 直接来源 | 高 |
| [S4] | wildwinter/screenplay-tools GitHub | 直接来源 | 高 |
| [S5] | piersdeseilligny/betterfountain GitHub | 直接来源 | 高 |
| [S6] | nyousefi/Fountain GitHub | 直接来源 | 高 |
| [S7] | ifrost/afterwriting-labs GitHub | 直接来源 | 中 |
| [S8] | trelby.org 官网 | 直接来源 | 高 |
| [S9] | kitscenarist.ru 官网 | 直接来源 | 中 |
| [S10] | arcstudiopro.com 官网 | 直接来源 | 高 |
| [S11] | writerduet.com 官网 | 直接来源 | 高 |
| [S12] | highland2.app 官网 | 直接来源 | 中 |
| [S13] | fadeinpro.com 官网 | 直接来源 | 中 |
| [S14] | finaldraft.com 官网 | 直接来源 | 高 |
| [S15] | laper.ai 首页（历史提取） | 直接来源 | 高→中 |
| [S16] | laper.ai/pricing/（历史提取） | 直接来源 | 高→中 |
| [S17] | laper.ai 博客系列 | 间接来源 | 中 |
| [S18] | melies.co 产品页 | 直接来源 | 高 |
| [S19] | scriptmatix.com 博客 | 直接来源 | 中高 |
| [S20] | pulserevops.com 工具排行 | 间接来源 | 中 |
| [S21] | arXiv 2502.16153v1（ACM CHI 2025）| 学术来源 | 高 |
| [S22] | wga.org AI 政策页 | 直接来源 | 高 |
| [S23] | storyflow.so「Best Final Draft Alternatives 2026」| 直接来源 | 中高 |
| [S24] | wifitalents.com 协作剧本软件综述 | 直接来源 | 中高 |
| [S25] | moyancn.com（有戏 XScript Pro）| 直接来源 | 高 |
| [S26] | blog.csdn.net 国内外剧本编辑工具合集 | 直接来源 | 中 |
| [S27] | Liveblocks 2025 富文本编辑器框架对比 | 直接来源 | 高 |
| [S28] | tiptap.dev 官方文档 | 直接来源 | 高 |
| [S29] | 微信公众号文章 | **不可访问** | N/A |
| [S30] | 小红书笔记 ① | **链接缺失** | N/A |
| [S31] | 小红书笔记 ② | **链接缺失** | N/A |
| [S32] | tron-comic 代码库（ScriptProcessor.tsx / llm.py / api.py） | 直接来源 | 极高 |
| [S33] | tron-comic 设计文档（structured-script-editor.md） | 直接来源 | 极高 |
| [S34] | Straits Research, Screen & Script Writing Software Market (2026) | 市场研究 | 高 |
| [S35] | tiptap.dev/docs/editor/extensions/custom-extensions | 直接来源 | 高 |
| [S36] | electric.ax/blog/2026/04/08/ai-agents-as-crdt-peers-with-yjs | 间接来源 | 中 |
| [S37] | finalbitai.com/blog/ai-screenwriting-tools-2026 | 直接来源 | 高 |
| [S38] | worldmetrics.org/best/scriptwriting-software/ | 直接来源 | 中高 |

---

## 十一、关键引用摘录

> **「AI 在早期构思阶段满意度达 100%，在对话生成阶段满意度为 0%。」**  
> — arXiv 2502.16153v1, ACM CHI 2025 [S21]

> **「63% of users would consider switching if a cloud-native alternative supported the same FDX format reliably.」**  
> — storyflow.so [S23]

> **「The global market reached USD 220.11 million in 2025, projected to reach USD 1,012.63 million by 2034 at CAGR 18.48%.」**  
> — Fortune Business Insights [S1]

> **「The screenplay is the source of truth.」**  
> — laper.ai 官网文案 [S15]

> **「Any text editor on any device.」**  
> — Fountain 设计哲学 [S2]

> **「The company can't require the writer to use AI software.」**  
> — WGA AI Policy [S22]

> **「Over 10 million tokens context support.」**  
> — melies.co [S18]

> **「Persistent Story Memory actively monitors character arcs and plot continuity across long manuscripts.」**  
> — FinalBit AI [S37]

> **「Asia Pacific anticipates the swiftest expansion, projected at 19.2% annually.」**  
> — Straits Research [S34]

> **「无需掌握任何剧本知识，即可开始创作。」**  
> — 有戏 XScript Pro [S25]

> **「Clean, validated Final Draft FDX and Fountain-compatible output.」**  
> — FinalBit AI [S37]

---

## 十二、未解决缺口与建议

| 缺口 | 影响 | 建议处置 |
|------|------|---------|
| 微信公众号文章正文缺失 | 可能遗漏本土舆情/国产工具评价 | 请用户手动复制正文或开启 Chrome 远程调试 |
| 小红书笔记链接缺失（×2） | 缺少社交媒体用户真实痛点 | 请用户补充 URL |
| laper.ai 实际产品体验缺失 | 功能列表基于官网文案推断 | T2 报告中标注「未经独立验证」 |
| 中文 Fountain 适配方案 | 需原型验证 | 技术方案评审时补充 PoC |
| FinalBit AI 详细定价 | 仅知「mid-to-upper range」 | 非阻塞，观察即可 |

---

*报告终：v5.0 为 task_yy3jyl3z56 的多源调研原始素材笔记，供 T2 综合分析与展示 HTML 撰写使用。*
