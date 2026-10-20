# LumenX Playground — 独立生成模块 PRD

> **版本**: v1.0  
> **日期**: 2026-06-06  
> **状态**: Draft  
> **作者**: StarLotus + Claude

---

## 1. 背景与动机

LumenX Studio 当前只支持"剧本 → 分镜 → 资产 → 视频 → 合成"的完整 pipeline 工作流。用户必须先创建项目、输入剧本，才能触达图像/视频生成能力。

但大量真实场景不需要完整 pipeline：

- 创作者想**快速试模型**：测试不同模型对同一 prompt 的效果差异
- 创作者想**生成独立素材**：一张角色设计图、一段动态测试视频，没有剧本上下文
- 创作者想**探索创意**：自由组合 prompt 和参考图，快速迭代，找到满意的视觉方向后再进入正式项目
- 创作者在项目进行中想**补充素材**：临时需要一张道具图或一段转场视频，不想走完整 pipeline

**Playground** 就是解决这个问题的模块——一个无项目上下文、纯粹的 AI 生成工具台。

---

## 2. 产品定位

```
LumenX 产品家族
├── LumenX Studio       ← pipeline-first 漫剧/视频生产
├── LumenX Atelier      ← graph-first 个人创作画布
├── LumenX Playground   ← ★ 独立生成工具台（本 PRD）
└── LumenX Core         ← 共享后端/模型路由/资产存储
```

**Playground 不是一个"产品"，是一个"工具模块"**。它是 LumenX 生态中最低门槛的入口：零配置、无项目、即开即用。生成的资产可以流向 Studio 或 Atelier。

---

## 3. 目标用户与场景

| 用户类型 | 场景 | 价值 |
|---------|------|------|
| 新用户 | 首次进入 LumenX，想快速体验 AI 生成能力 | 降低上手门槛，30 秒内看到第一张 AI 生成图 |
| 独立创作者 | 生成单独的角色设计图、场景概念图 | 不必创建项目即可产出素材 |
| 视频创作者 | 快速测试不同视频模型的运动效果 | 对比 Seedance / Kling / Vidu 的差异 |
| Studio 用户 | 项目中临时需要补充素材 | 在 Playground 生成后收藏到资产库，再在项目中引用 |
| 模型探索者 | 同一 prompt 跑多个模型对比 | 批量抽卡，快速选出最佳模型 |

---

## 4. 功能规格

### 4.1 生成能力（v1 全覆盖）

| 模式 | 输入 | 输出 | 可用模型 |
|------|------|------|---------|
| **文生图 (t2i)** | prompt | 图片 | gpt-image-2, qwen-image-2.0-pro, wan2.2/2.5-t2i |
| **图生图 (i2i)** | prompt + 参考图 | 图片 | gpt-image-2, qwen-image-2.0-pro, wan2.5-i2i |
| **文生视频 (t2v)** | prompt | 视频 | seedance-2.0-t2v, happyhorse-1.0-t2v |
| **图生视频 (i2v)** | prompt + 首帧图 | 视频 | kling-v3, seedance-2.0, vidu, wan2.x, pixverse, happyhorse |
| **参考图视频 (r2v)** | prompt + 参考图 + 可选首帧 | 视频 | kling-v3, seedance-2.0, vidu, wan2.6, pixverse, happyhorse |
| **视频编辑 (v2v)** | prompt + 源视频 | 视频 | happyhorse-1.0-video-edit |

模型列表从 `config/model_catalog/generated/model_catalog.json` 动态读取，Playground 不硬编码模型 ID。

### 4.2 批量生成（抽卡）

- 用户可设置"生成数量"（1-4 张/条），一次生成多个候选
- 候选结果以网格形式展示在结果画廊中
- 每个候选可独立预览、下载、收藏、送入下一步

### 4.3 Prompt 模板与历史

**Prompt 历史**：
- 自动记录每次生成使用的 prompt
- 历史列表按时间倒序，支持搜索
- 一键复制到当前输入框
- 可将任意历史 prompt 存为模板

**Prompt 模板**：
- 用户可创建自定义模板（名称 + prompt 文本 + 可选的默认参数）
- 模板列表支持分类管理（图像 / 视频 / 通用）
- 一键套用模板到当前输入
- 模板持久化存储于 `output/playground_templates.json`

### 4.4 结果管理

- 生成结果实时出现在右侧画廊
- 持久化到 `output/playground_history.json`
- 每个结果卡片的操作：
  - **预览**：图片放大 / 视频播放（复用 LightboxProvider）
  - **下载**：保存到本地
  - **收藏到资产库**：打上 `source: playground` 标签存入共享资产库
  - **生成视频**（仅图片结果）：一键切到 i2v 模式，自动填入首帧
  - **删除**：从历史中移除

### 4.5 资产库双向打通

**Playground → 资产库**：
- 结果卡片上"收藏到资产库"按钮
- 收藏时可选择分类（角色/场景/道具/通用素材）
- 资产库中标记来源为 `playground`

**资产库 → Playground**：
- 在 i2i / i2v / r2v / v2v 模式的媒体上传区
- 除了本地上传，增加"从资产库选取"按钮
- 打开资产库选择器（modal），可按分类筛选

---

## 5. UI/UX 设计

### 5.1 入口

- **首页卡片**：Home 页增加 Playground 入口卡片（与 Library、Settings 同级）
- **导航**：顶部 breadcrumb 或侧边栏增加 Playground 入口
- **路由**：`#/playground`

### 5.2 布局（左右分栏）

参考可灵（图2）的暗色分栏布局，融合 LumenX 的 glassmorphism 设计语言：

```
┌─────────────────────────────────────────────────────────┐
│  LumenX > Playground                                    │
├──────────────────────────┬──────────────────────────────┤
│                          │                              │
│  ┌─ 模式 Tab ──────────┐ │   生成结果画廊               │
│  │ 文生图 │ 图生图 │ ...│ │                              │
│  └──────────────────────┘ │   ┌──────┐ ┌──────┐        │
│                          │   │ 结果1 │ │ 结果2 │        │
│  ┌─ 模型选择器 ────────┐ │   │      │ │      │        │
│  │ ● Seedance 2.0  ▾  │ │   │ [预览]│ │ [预览]│        │
│  └──────────────────────┘ │   │ [↗i2v]│ │ [★收] │        │
│                          │   └──────┘ └──────┘        │
│  ┌─ 媒体输入（按模式） ─┐ │                              │
│  │ [+ 上传首帧图]      │ │   ┌──────┐ ┌──────┐        │
│  │ [📁 从资产库选取]    │ │   │ 结果3 │ │ 结果4 │        │
│  └──────────────────────┘ │   └──────┘ └──────┘        │
│                          │                              │
│  ┌─ Prompt 输入 ───────┐ │   ── 历史分割线 ──           │
│  │                      │ │                              │
│  │ [📋模板] [🕐历史]   │ │   ┌──────┐ ┌──────┐        │
│  └──────────────────────┘ │   │ 旧结果 │ │ ...  │        │
│                          │   └──────┘ └──────┘        │
│  ┌─ 参数栏 ────────────┐ │                              │
│  │ 16:9 ▾ │ 720P │ 5s  │ │                              │
│  │ 数量: [1] [2] [4]   │ │                              │
│  │ ▸ 高级参数           │ │                              │
│  └──────────────────────┘ │                              │
│                          │                              │
│  [════ 生成 ════════════] │                              │
│                          │                              │
├──────────────────────────┴──────────────────────────────┤
```

### 5.3 设计规范

- 遵循 LumenX 暗色主题（`#050508` 背景）
- 面板使用 glassmorphism（5% white + backdrop-blur）
- 主色 Electric Blue `#646cff`，强调色 Hot Pink `#ff0080`
- 结果画廊使用 Framer Motion 的 staggered 入场动画
- 模式 Tab 使用 LumenX 品牌渐变高亮
- 生成中状态：卡片骨架屏 + 进度指示

---

## 6. 技术架构

### 6.1 后端

**新模块**：`src/apps/playground/`

```
src/apps/playground/
├── __init__.py
├── api.py              # FastAPI router（/playground/*）
├── models.py           # Pydantic 数据模型
├── service.py          # 生成编排逻辑
└── storage.py          # 历史记录 & 模板持久化
```

**路由挂载**：在主 app 中 `app.include_router(playground_router, prefix="/playground")`

**核心端点**：

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/playground/generate` | 统一生成入口 |
| `GET` | `/playground/history` | 获取历史记录（分页） |
| `GET` | `/playground/history/{id}` | 获取单条记录详情 |
| `GET` | `/playground/history/{id}/status` | 轮询生成状态 |
| `DELETE` | `/playground/history/{id}` | 删除历史记录 |
| `POST` | `/playground/history/{id}/save-to-library` | 收藏到资产库 |
| `GET` | `/playground/templates` | 获取模板列表 |
| `POST` | `/playground/templates` | 创建模板 |
| `PUT` | `/playground/templates/{id}` | 更新模板 |
| `DELETE` | `/playground/templates/{id}` | 删除模板 |

**生成流程**（`service.py`）：
```
POST /playground/generate
  → 校验 mode + model_id + prompt
  → 通过 factory.py 获取模型适配器
  → 创建 PlaygroundGeneration 记录（status: pending）
  → 启动后台任务调用模型适配器
  → 返回 generation_id
  → 客户端轮询 /status 直到 completed/failed
```

### 6.2 数据模型

```python
class PlaygroundMode(str, Enum):
    T2I = "t2i"
    I2I = "i2i"
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"
    V2V = "v2v"

class PlaygroundGeneration(BaseModel):
    id: str
    mode: PlaygroundMode
    model_id: str
    prompt: str
    negative_prompt: Optional[str] = None
    input_media: List[str] = []         # 输入媒体文件路径
    parameters: dict = {}               # 分辨率、时长、宽高比等
    batch_size: int = 1                 # 生成数量
    outputs: List[PlaygroundOutput] = []
    status: str = "pending"             # pending / running / completed / failed
    error: Optional[str] = None
    created_at: str
    saved_to_library: bool = False

class PlaygroundOutput(BaseModel):
    id: str
    media_path: str                     # 生成结果文件路径
    media_type: str                     # "image" | "video"
    thumbnail_path: Optional[str] = None
    saved_to_library: bool = False

class PlaygroundTemplate(BaseModel):
    id: str
    name: str
    category: str = "general"           # image / video / general
    prompt: str
    negative_prompt: Optional[str] = None
    default_mode: Optional[PlaygroundMode] = None
    default_model_id: Optional[str] = None
    default_parameters: dict = {}
    created_at: str
    updated_at: str
```

**持久化文件**：
- `output/playground_history.json` — 生成历史
- `output/playground_templates.json` — Prompt 模板

### 6.3 前端

**新模块**：`frontend/src/components/modules/playground/`

```
frontend/src/components/modules/playground/
├── PlaygroundPage.tsx          # 主页面（左右分栏）
├── ModeSelector.tsx            # 模式切换 Tab
├── InputPanel.tsx              # 左侧输入面板
├── ModelSelector.tsx           # 模型选择器
├── MediaInput.tsx              # 媒体输入区（上传 + 从资产库选取）
├── PromptInput.tsx             # Prompt 输入框（含模板/历史按钮）
├── ParameterBar.tsx            # 参数配置栏（常用 + 高级折叠）
├── ResultGallery.tsx           # 右侧结果画廊
├── ResultCard.tsx              # 单个结果卡片
├── PromptTemplateModal.tsx     # 模板管理弹窗
├── PromptHistoryDrawer.tsx     # 历史 Prompt 抽屉
├── AssetPickerModal.tsx        # 从资产库选取素材弹窗
└── usePlaygroundStore.ts       # Zustand store
```

**Store 结构**（`usePlaygroundStore.ts`）：
```typescript
interface PlaygroundState {
  // 当前输入状态
  mode: PlaygroundMode
  modelId: string
  prompt: string
  negativePrompt: string
  inputMedia: File[]
  parameters: Record<string, any>
  batchSize: number

  // 模型偏好记忆（mode → modelId）
  modelPreferences: Record<PlaygroundMode, string>

  // 生成历史
  history: PlaygroundGeneration[]

  // 模板
  templates: PlaygroundTemplate[]

  // UI 状态
  isGenerating: boolean
  activeGenerationIds: string[]
}
```

**API 客户端**：在 `frontend/src/lib/api.ts` 中新增 `playground` 命名空间的方法。

### 6.4 生成结果存储

```
output/
├── playground/                  # ★ Playground 专属输出目录
│   ├── images/                  # 生成的图片
│   └── videos/                  # 生成的视频
├── playground_history.json      # 历史记录
└── playground_templates.json    # 模板数据
```

---

## 7. 资产库集成细节

### 7.1 收藏到资产库

用户点击结果卡片的"收藏"按钮时：
1. 调用 `POST /playground/history/{id}/save-to-library`
2. 后端将文件复制/移动到 `output/assets/` 对应目录
3. 在资产库数据中创建条目，标记 `source: "playground"`、`playground_generation_id`
4. 前端更新 `saved_to_library` 状态，按钮变为已收藏样式

### 7.2 从资产库选取

在需要媒体输入的模式（i2i / i2v / r2v / v2v）：
1. 输入区显示"从资产库选取"按钮
2. 点击打开 `AssetPickerModal`
3. Modal 内复用资产库的筛选/浏览逻辑
4. 选中后将资产路径填入输入区

---

## 8. 实施计划

### Phase 1 — 核心骨架（可跑通）
1. 后端 `src/apps/playground/` 模块搭建 + `/playground/generate` + `/playground/history`
2. 前端 `PlaygroundPage` 左右分栏 + `ModeSelector` + `PromptInput` + `ModelSelector`
3. 实现 t2i 模式完整链路（prompt → 生成 → 结果展示）
4. `#/playground` 路由注册 + 首页入口卡片
5. 验证：能通过 Playground 生成一张图并看到结果

### Phase 2 — 全模式 + 批量
6. 补全 i2i / t2v / i2v / r2v / v2v 模式的输入面板和参数适配
7. 批量生成（抽卡）功能
8. `MediaInput` 组件（本地上传 + 文件预览）
9. `ParameterBar`（常用参数 + 高级折叠）
10. 验证：所有 6 种模式可正常生成

### Phase 3 — 结果管理 + 资产库
11. `ResultCard` 操作按钮（预览/下载/收藏/生成视频）
12. 图→视频一键衔接流程
13. 收藏到资产库 API + 前端集成
14. 从资产库选取素材 `AssetPickerModal`
15. 验证：t2i 生成图 → 收藏 → 在 i2v 中从资产库选取该图 → 生成视频

### Phase 4 — 模板与历史
16. Prompt 历史记录（自动收集 + 搜索 + 一键复制）
17. Prompt 模板 CRUD（创建/编辑/删除/套用）
18. `PromptTemplateModal` + `PromptHistoryDrawer`
19. 模型偏好记忆持久化
20. 验证：创建模板 → 切换模式 → 套用模板 → 生成

---

## 9. 不做的事情（v1 边界）

- **不做项目关联**：Playground 生成的资产不自动关联到任何 Studio 项目
- **不做 Agent 模式**：不集成 Atelier 的 Agent 规划能力
- **不做协作**：单用户使用，不考虑多用户共享历史
- **不做计费/积分**：本地部署产品，不需要积分系统
- **不做 prompt 智能优化**：v1 不集成 LLM 对 prompt 的自动润色（Studio 有此功能，Playground 后续可加）
- **不做视频拼接/合成**：Playground 只做单次生成，不做多段视频合并

---

## 10. 成功指标

| 指标 | 目标 |
|------|------|
| 首次生成耗时 | 进入 Playground → 看到第一个结果 < 60 秒（不含模型推理时间） |
| 模式覆盖 | 6 种模式全部可用 |
| 图→视频转化 | 一键衔接流程可用，无需手动切换+重新上传 |
| 资产库打通 | 双向流通：收藏 + 选取 |
