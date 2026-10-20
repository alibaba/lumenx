# LumenX 三大功能设计方案

> 评估日期：2026-03-11
> 优先级：需求 3 → 需求 2 → 需求 1

---

## 需求 3：AI 交互式多轮润色（★☆☆☆☆）

### 背景

现有润色流程是单轮的：用户点击"AI 润色" → LLM 生成 → 应用或放弃。没有反馈通道，不满意只能手动改写或重新生成。

### 方案：追加指令模式

无状态设计。每次"再润色"时，将当前提示词 + 用户反馈拼成一个新的单轮请求。不维护对话历史。

### 涉及的润色接口

| API | 方法 | 文件位置 |
|-----|------|----------|
| `POST /storyboard/refine_prompt` | `polish_storyboard_prompt()` | llm.py:713 (Prompt C) |
| `POST /video/polish_prompt` | `polish_video_prompt()` | llm.py:802 (Prompt D) |
| `POST /video/polish_r2v_prompt` | `polish_r2v_prompt()` | llm.py:864 (Prompt E) |

### 后端改动

三个润色 API 的 Request Model 增加可选字段：

```python
class RefinePromptRequest(BaseModel):
    frame_id: str
    raw_prompt: str
    assets: list = []
    feedback: str = ""  # 新增：用户反馈，空则为首次润色
```

LLM 调用时，如果 `feedback` 非空，在 system prompt 后追加：

```
当前提示词: {current_prompt}
用户反馈: {feedback}
请根据用户反馈修改提示词，只改需要改的部分，保持其他部分不变。
```

### 前端改动

- 润色结果展示区增加一个文本输入框 + "再润色"按钮
- 输入框 placeholder: "哪里不满意？描述你的修改意见..."
- 点击"再润色"时，将当前提示词作为 `raw_prompt`、用户输入作为 `feedback` 调用同一个 API
- 首次润色时 feedback 为空，走原有逻辑

### 交互流程

```
用户点击"AI 润色"
  → API 调用 (feedback="")
  → 展示结果 + 输入框
  → 用户输入"镜头运动改成从左到右平移"
  → 点击"再润色"
  → API 调用 (raw_prompt=当前结果, feedback=用户输入)
  → 展示新结果
  → 满意则点击"应用"
```

---

## 需求 2：自定义润色提示词 C/D/E（★★☆☆☆）

### 背景

Prompt C/D/E 硬编码在 llm.py 中。高级用户希望能自定义这些提示词来控制润色风格和质量。

### 方案：Series 默认 + Episode 覆盖

两级继承：Series 设置默认提示词 → Episode 可覆盖。读取时优先 Episode 级，fallback 到 Series 级，再 fallback 到系统内置默认值。

**注意**：此功能依赖需求 1 的 Series 架构。在 Series 未实现前，可先以 Project 级别实现（每个项目可自定义提示词），Series 上线后再补继承逻辑。

### 数据存储

```python
# 提示词配置结构
class PromptConfig(BaseModel):
    storyboard_polish: str = ""   # Prompt C 覆盖
    video_polish: str = ""        # Prompt D 覆盖
    r2v_polish: str = ""          # Prompt E 覆盖

# Project / Episode 级别
class Script(BaseModel):
    # ... existing fields ...
    prompt_config: PromptConfig = PromptConfig()

# 未来 Series 级别
class Series(BaseModel):
    # ... other fields ...
    prompt_config: PromptConfig = PromptConfig()
```

### 读取优先级

```python
def get_effective_prompt(prompt_type: str, episode: Script, series: Series = None) -> str:
    """三级 fallback: Episode → Series → 系统默认"""
    episode_value = getattr(episode.prompt_config, prompt_type, "")
    if episode_value.strip():
        return episode_value

    if series:
        series_value = getattr(series.prompt_config, prompt_type, "")
        if series_value.strip():
            return series_value

    return SYSTEM_DEFAULT_PROMPTS[prompt_type]
```

### 后端改动

- `models.py`: 新增 `PromptConfig` 模型
- `llm.py`: `polish_storyboard_prompt` / `polish_video_prompt` / `polish_r2v_prompt` 接受可选的 `custom_system_prompt` 参数，非空时替代内置 prompt
- `api.py`: 新增 `GET/PUT /projects/{id}/prompt_config` 接口
- `pipeline.py`: 调用润色时从 project 读取 prompt_config

### 前端改动

- 项目设置中新增"提示词配置"区域（可折叠）
- 三个提示词各一个 textarea 编辑器
- 显示 placeholder 为系统默认值，方便用户参考
- "恢复默认"按钮清空自定义值

---

## 需求 1：文件导入 + 集数划分 + Series 架构（★★★★☆）

### 背景

目前一个 Project 对应一段脚本文本，项目间资产完全隔离。无法做多集连续剧制作，也无法跨项目共享角色。

### 方案概述

1. **文件导入**：支持 txt/md 文件上传
2. **智能集数划分**：LLM 按叙事节奏拆分，用户指定集数仅作建议
3. **Series 架构**：新增 Series 层，包含共享资产库 + 多个 Episode
4. **跨系列导入**：支持从另一个 Series 批量导入资产

### 数据模型

```python
class Series(BaseModel):
    id: str
    title: str
    description: str = ""
    created_at: str
    updated_at: str

    # 共享资产库
    characters: List[Character] = []
    scenes: List[Scene] = []
    props: List[Prop] = []

    # 统一视觉风格
    art_direction: Optional[ArtDirection] = None

    # Series 级提示词配置（需求 2）
    prompt_config: PromptConfig = PromptConfig()

    # 集数列表（引用 Episode/Script ID）
    episode_ids: List[str] = []


class Script(BaseModel):  # 即 Episode
    # ... existing fields ...
    series_id: Optional[str] = None  # 所属系列

    # Episode 的 characters/scenes/props 改为引用 Series 资产的 ID
    # 或保持独立副本 + 从 Series 同步机制
```

### 资产共享策略

**引用模式**（推荐）：
- Series 持有资产原件
- Episode 引用 Series 资产 ID，不复制
- Episode 可添加 Episode-only 资产（仅该集出现的临时角色）
- 修改 Series 资产自动影响所有 Episode

### 文件导入流程

```
用户上传 txt/md 文件
  → 后端解析文件内容
  → 用户选择：创建新项目 / 创建新系列（含集数建议）
  → 如果创建系列：
    → LLM 分析全文，按叙事节奏划分集数
    → 返回划分预览（每集标题 + 内容摘要 + 字数）
    → 用户确认/调整
    → 批量创建 Series + Episodes
    → 对全文提取共享资产库（角色/场景/道具）
```

### 智能划分 Prompt 设计

```
你是一名专业的剧本编剧和分集策划师。

请将以下小说/剧本文本按叙事节奏划分为约 {suggested_episodes} 集。

划分原则：
1. 每集应有完整的叙事弧（开端/发展/高潮或悬念）
2. 在自然的情节转折点或场景切换处分集
3. 各集内容量大致均衡，但优先保证叙事完整性
4. 实际集数可以在建议集数 ±2 范围内浮动

输出 JSON:
{
  "episodes": [
    {
      "episode_number": 1,
      "title": "集标题",
      "summary": "50字以内的内容摘要",
      "start_marker": "该集起始的原文前20字",
      "end_marker": "该集结束的原文后20字",
      "estimated_duration": "预估时长（分钟）"
    }
  ]
}
```

### 跨系列资产导入

```
用户进入 Series A 的资产管理页
  → 点击"从其他系列导入"
  → 选择 Series B
  → 展示 Series B 的资产列表（勾选）
  → 确认导入
  → 复制选中资产到 Series A（深拷贝，独立于原系列）
```

### 关键 API 端点

```
# Series CRUD
POST   /series                          # 创建系列
GET    /series                          # 列表
GET    /series/{id}                     # 详情（含资产和集数）
PUT    /series/{id}                     # 更新
DELETE /series/{id}                     # 删除

# 文件导入
POST   /series/import                   # 上传文件 + 智能划分
POST   /series/{id}/episodes            # 手动添加集数

# 资产管理
GET    /series/{id}/assets              # 获取共享资产库
POST   /series/{id}/assets/import       # 从其他系列导入资产

# Episode 关联
GET    /series/{id}/episodes            # 获取集数列表
```

### 前端改动（大）

- 新增 Series 列表页（替代或并列现有项目列表）
- Series 详情页：资产管理 + 集数列表 + 统一风格配置
- Episode 页面：基本复用现有项目页面，资产从 Series 引用
- 文件导入对话框：上传 → 预览划分 → 确认
- 跨系列导入对话框

### 存储

```
~/.tron/comic/
├── projects.json    # 现有，保持兼容
└── series.json      # 新增
```

### 向后兼容

- 不属于任何 Series 的 Project 继续独立存在（series_id = None）
- 现有项目不受影响，可通过"加入系列"功能后续归入

---

## 实施路线

```
Phase 1: 需求 3 — AI 交互式多轮润色
  ├── 后端：3 个润色 API 增加 feedback 参数
  ├── 前端：润色结果区增加反馈输入框 + 再润色按钮
  └── 预估：1-2 天

Phase 2: 需求 2 — 自定义润色提示词（Project 级先行）
  ├── 后端：PromptConfig 模型 + API + 润色方法改造
  ├── 前端：项目设置中提示词编辑 UI
  └── 预估：2-3 天

Phase 3: 需求 1 — Series 架构 + 文件导入
  ├── 后端：Series 数据模型 + API + 资产共享 + 文件导入 + LLM 划分
  ├── 前端：Series 列表/详情页 + 导入流程 + 资产管理 UI 重构
  ├── 数据迁移：现有项目兼容
  └── 预估：1-2 周

Phase 2.5（Phase 3 完成后回补）:
  └── 将需求 2 的 PromptConfig 提升到 Series 级，加继承覆盖逻辑
```

---

## 设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 集数划分方式 | 叙事驱动（集数为建议值） | 保证叙事完整性比精确集数更重要 |
| 资产共享范围 | Series 级别（非全局） | 心智模型清晰，避免变成资产管理工具 |
| 跨系列复用 | 深拷贝导入 | 避免跨系列引用带来的级联修改问题 |
| 润色提示词层级 | Series 默认 + Episode 覆盖 | 兼顾一致性和灵活性 |
| 多轮润色方式 | 追加指令（无状态） | 简洁，润色通常 2-3 轮即定稿 |
| Episode 与 Project 关系 | Episode 复用 Script 模型 | 最小化改动，现有 pipeline 逻辑复用 |
