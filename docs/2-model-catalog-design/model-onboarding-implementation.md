# LumenX 模型接入系统实现说明

这份文档解释当前仓库里已经落地的模型接入系统是怎么工作的，它解决了什么问题，哪些地方已经自动化，哪些地方仍然需要人来判断。

如果你只想知道“以后我要接新模型时应该从哪里开始”，先看这三项：

1. 入口 workflow：[`/lumenx-model-onboarding`](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/.codex/workflows/lumenx-model-onboarding.md)
2. 构建命令：`python scripts/build_model_catalog.py`
3. 校验命令：`python scripts/validate_model_catalog.py`

---

## 1. 这个系统到底解决什么问题

在这套系统落地之前，LumenX 对模型的支持信息分散在很多地方：

- 有些模型字符串写在后端默认值里
- 有些模型列表写在前端下拉框里
- 有些参数能力靠代码里的 `if model == ...`
- 文档更新和代码更新之间没有一个固定入口

这会导致一个很实际的问题：

当你更新模型版本，或者接入一个新模型时，你不知道自己是不是已经把所有地方都改全了。

现在这套系统把这个过程拆成了三层：

1. **文档证据层**
   - 记录厂商原始文档或本地 staging 证据
2. **可执行清单层**
   - 用 `model_catalog` 描述“LumenX 实际支持什么”
3. **消费层**
   - 后端和前端都从生成后的 catalog artifact 读取模型信息

这样做的结果是：

- 模型支持信息有了统一入口
- 默认值、下拉列表、参数能力不再散落
- 每次改完都能跑固定的构建和校验

---

## 2. 系统的运行路径

一次完整的模型接入或模型更新，现在应该沿着这条路径走：

1. 用户触发 `/lumenx-model-onboarding`
2. 抓取或整理模型文档证据
3. 更新 `config/model_catalog/` 下的 YAML
4. 运行构建脚本，生成后端和前端要消费的 JSON
5. 运行校验脚本，检查 artifact 一致性、默认值可见性、文档链路
6. 如果需要，再补运行时逻辑或前端 UI
7. 跑测试与构建，确认整条链路没有被破坏

前端类型检查现在推荐统一走 `cd frontend && npm run typecheck`，不要再直接手敲 `npx tsc --noEmit`。原因是 Next.js 的一部分类型文件会生成在 `.next/types`，而干净 checkout 里这个目录一开始可能并不存在。新的包装脚本会先检查这一点，必要时自动补一次 `next build`，再继续做 TypeScript 校验。

这里最重要的一点是：

**不是所有模型变更都需要改运行时代码。**

很多变更只是：

- 新增 model ID
- 修改默认模型
- 调整某个模型在 UI 中是否显示
- 更新支持的参数和文档链路

这些事情现在都可以只改 catalog。

但如果文档显示下面这些东西变了，就不能只改 catalog：

- 新认证方式
- 新 endpoint
- 新的媒体输入方式
- 新的请求字段 / 响应字段
- 新 provider family

这时候还要继续改后端 adapter 或前端参数 UI。

---

## 3. 两种工作模式

### 模式 A：完整多仓模式

这是理想模式。

你同时拥有：

- 原始厂商文档归档仓库
- Context Hub 源仓库
- 当前 LumenX 代码仓库

这种模式下，三层都能真正同步。

### 模式 B：仅当前仓库模式

这是当前最常见的工程模式。

你只有当前 LumenX 仓库，于是：

- 原始文档先抓到 `docs/1-api-reference/`
- `model_catalog` 在本仓库更新
- 在实现说明或 PR 中注明：外部 raw archive / Context Hub 还需要后续补同步

模式 B 不影响代码开发和验证，但要明确它不是“所有文档仓库都已经同步完成”的意思。

---

## 4. 现在仓库里每个关键文件是干什么的

下面这张表只讲和“模型接入系统”直接相关的文件。

### 4.1 Workflow 与路由层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [AGENTS.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/AGENTS.md) | 告诉 Codex 这个仓库有哪些 workflow 入口，以及用户说什么话时该触发哪个 workflow | 新增了 `/lumenx-model-onboarding` 的触发规则和文件映射 |
| [.codex/workflows/lumenx-model-onboarding.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/.codex/workflows/lumenx-model-onboarding.md) | Codex 侧的正式模型接入 workflow | 新增，定义了模型接入的步骤、范围判断、验证要求、停顿条件 |
| [.claude/commands/lumenx-model-onboarding.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/.claude/commands/lumenx-model-onboarding.md) | Claude 侧的同名流程镜像 | 新增，保持和 Codex workflow 行为一致 |

### 4.2 文档证据与设计层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [docs/2-model-catalog-design/plans/2026-04-03-model-docs-and-catalog-architecture.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/2-model-catalog-design/plans/2026-04-03-model-docs-and-catalog-architecture.md) | 最初的架构设计文档，回答“为什么要做这套系统” | 已同步到真实实现，补了前端本地 mirror 和校验入口 |
| [docs/2-model-catalog-design/plans/2026-04-04-lumenx-model-onboarding-system.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/2-model-catalog-design/plans/2026-04-04-lumenx-model-onboarding-system.md) | 本轮实现计划文档，回答“这次具体要做哪些落地工作” | 新增 |
| [docs/2-model-catalog-design/model-onboarding-implementation.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/2-model-catalog-design/model-onboarding-implementation.md) | 当前这份实现说明，解释系统已经怎么工作、每个文件负责什么 | 新增 |
| [docs/1-api-reference/README.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/1-api-reference/README.md) | 解释 `docs/1-api-reference/` 的角色，避免误把它当成 canonical archive | 新增 |
| [docs/1-api-reference/*.md](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/docs/1-api-reference) | 仓库内的文档证据区 / 本地 staging 区 | 继续保留，用于 repo-only 模式下的文档证据 |

### 4.3 Catalog 源文件层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [config/model_catalog/catalog.meta.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/catalog.meta.yaml) | 定义 catalog 的版本和默认模型 | 已作为 canonical source 启用 |
| [config/model_catalog/families/wan.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/wan.yaml) | Wan family 的模型、参数、文档链路、UI 暴露 | 已启用 |
| [config/model_catalog/families/kling.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/kling.yaml) | Kling family 的 catalog 定义 | 已启用 |
| [config/model_catalog/families/vidu.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/vidu.yaml) | Vidu family 的 catalog 定义 | 已启用 |
| [config/model_catalog/families/pixverse.yaml](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/families/pixverse.yaml) | PixVerse family 的 catalog 占位与规划定义 | 已启用，当前仍是 planned / hidden 路径 |

### 4.4 生成物层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [config/model_catalog/generated/model_catalog.json](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/generated/model_catalog.json) | 后端 canonical artifact，给 Python 运行时使用 | 已生成并纳入工作流 |
| [frontend/src/generated/modelCatalog.json](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/generated/modelCatalog.json) | 前端本地 mirror artifact，给 Next.js/TypeScript 使用 | 已生成并纳入工作流 |
| [config/model_catalog/schema/model-catalog.schema.json](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/config/model_catalog/schema/model-catalog.schema.json) | catalog schema stub，用于结构说明 | 已生成 |

### 4.5 构建与校验工具层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [scripts/build_model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/build_model_catalog.py) | 从 YAML 生成后端 JSON、前端 mirror、schema | 已打通双 artifact 生成，并给出下一步校验提示 |
| [scripts/validate_model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/scripts/validate_model_catalog.py) | 输出可读的 catalog 校验报告 | 新增，用来校验 artifact 一致性、默认值可见性、文档链路 |
| [frontend/scripts/typecheck.mjs](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/scripts/typecheck.mjs) | 前端稳定类型检查入口 | 新增，解决干净 checkout 下 `.next/types` 尚未生成时 `tsc` 直接失败的问题 |

### 4.6 后端消费层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [src/utils/model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/model_catalog.py) | catalog 的核心 Python 工具层，负责读取、构建、生成、校验 | 这是整套系统的核心工具文件，本轮补上了前端 mirror 支持和 validation report 逻辑 |
| [src/utils/provider_registry.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/utils/provider_registry.py) | provider family routing 定义 | 已改为优先从 catalog 派生 family config，保留安全 fallback |
| [src/apps/comic_gen/models.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/src/apps/comic_gen/models.py) | 项目默认模型设置 | 已改为从 catalog 默认值读取 |

### 4.7 前端消费层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [frontend/src/lib/modelCatalog.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/modelCatalog.ts) | 前端 catalog 适配层 | 这是前端模型系统的核心入口，负责把生成 JSON 转成前端可直接用的列表、默认值、fallback、R2V 选择逻辑 |
| [frontend/src/store/projectStore.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/store/projectStore.ts) | 前端历史模型常量与类型出口 | 现在主要改成 re-export catalog 驱动的数据，而不是继续内嵌模型硬编码 |
| [frontend/src/components/common/ModelSettingsModal.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/common/ModelSettingsModal.tsx) | 项目级模型设置弹窗 | 默认值与回填逻辑改为来自 catalog |
| [frontend/src/components/series/SeriesModelSettingsModal.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/series/SeriesModelSettingsModal.tsx) | 系列级模型设置弹窗 | 默认值与列表改为来自 catalog |
| [frontend/src/components/settings/SettingsPage.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/settings/SettingsPage.tsx) | 全局设置页 | 默认模型保存与读取改为使用 catalog 统一来源 |
| [frontend/src/components/modules/VideoGenerator.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoGenerator.tsx) | 视频生成页的默认 I2V 入口 | 默认模型改为通过 catalog 解析 |
| [frontend/src/components/modules/VideoSidebar.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoSidebar.tsx) | 视频参数侧边栏 | 模型切换、可选模型、R2V 选择逻辑改为 catalog 驱动 |
| [frontend/src/components/modules/VideoCreator.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/VideoCreator.tsx) | 具体发起 I2V / R2V 任务的页面 | R2V 的“显示模型”和“真实路由模型”已分离，并由 catalog 决定 |
| [frontend/src/components/modules/PropertiesPanel.tsx](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/components/modules/PropertiesPanel.tsx) | 分镜参考图选择与限制逻辑 | 参考图上限不再写死 `wan2.6-image=4`，而是从 catalog 读取 |
| [frontend/src/lib/api.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/lib/api.ts) | 前端调用后端的 API 封装 | 默认 I2V 模型改为来自 catalog |

### 4.8 测试层

| 文件 | 作用 | 这次做了什么 |
|------|------|-------------|
| [tests/test_model_catalog.py](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/tests/test_model_catalog.py) | 后端 catalog 测试 | 已补到覆盖构建、前端 mirror、一致性、default visibility regression、validation report |
| [frontend/src/__tests__/model-catalog.test.ts](/Users/hoshinoren/Documents/code/project/video_gen/gitlab/tron-comic/frontend/src/__tests__/model-catalog.test.ts) | 前端 catalog 测试 | 已补到覆盖默认值、可见模型、hidden/planned 过滤、fallback、R2V 逻辑 |

---

## 5. 现在已经自动化了什么

下面这些事情已经是系统能力，不用再靠人肉记忆：

- 从 YAML 生成后端和前端两份 artifact
- 后端默认模型从 catalog 读取
- 前端模型列表和默认值从 catalog 读取
- 旧配置里无效 model ID 会自动 fallback 到当前 catalog 可见默认值
- R2V 的选择模型和真正发请求的 route model 可以分离
- 参考图上限可以从 catalog 的 `inputs` 元数据读取
- 可以跑独立的 validation report 看 catalog 有没有明显断裂

---

## 6. 现在还没有完全自动化的部分

为了避免误解，这里把仍然需要人工判断的部分说清楚。

### 6.1 还需要人工判断的事情

- 新 provider family 是否值得接入
- 文档是否可信、是否完整
- 某个字段变化到底是 catalog 变化，还是运行时协议变化
- 新参数是否值得在前端暴露
- UI 上该怎样向用户表达某个模型的差异

### 6.2 还不能只靠 catalog 解决的事情

- 新 auth 签名流程
- 新 endpoint 协议
- 新媒体输入方式
- 新 provider-specific polling / callback 行为
- 新模型需要前端提供全新的参数输入控件

这也是为什么 workflow 文档里有“必须停下来问用户”的条件。

---

## 7. 以后接入新模型时，最推荐的操作顺序

如果是一次常规模型更新，直接照下面做：

1. 触发 `/lumenx-model-onboarding`
2. 抓文档证据到外部 raw archive，或者当前仓库 `docs/1-api-reference/`
3. 更新 `config/model_catalog/families/*.yaml`
4. 运行 `python scripts/build_model_catalog.py`
5. 运行 `python scripts/validate_model_catalog.py`
6. 运行 `cd frontend && npm run typecheck`
7. 如果文档表明协议没变，只需跑测试并结束
8. 如果协议变了，再继续改 `src/models/`、`src/utils/provider_media.py`、前端参数 UI

---

## 8. 你最应该关心的两个判断

### 判断一：这次是不是只改 catalog 就够了？

如果只是这些：

- model id 更新
- 默认值切换
- 参数元数据变化
- 文档链路变化
- visible_in / recommended / badges 变化

大概率是 **catalog-only**。

### 判断二：这次是不是已经真的完成了？

至少同时满足：

- build 脚本通过
- validate 脚本通过
- pytest 通过
- 前端 `npm run typecheck` 通过
- 前端 `test:all` 通过
- 前端 build 通过

只过了其中一两个，不算真正完成。
