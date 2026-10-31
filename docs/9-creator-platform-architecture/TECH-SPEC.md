# LumenX 创作者生态与任务商业化平台技术方案规范
## (LumenX Creator Ecosystem & Commercial Task Marketplace)

> **文档状态**: 终审修订中 / 开发前门禁未满足  
> **文档路径**: `docs/9-creator-platform-architecture/TECH-SPEC.md`  
> **修订日期**: 2026-10-31  
> **设计目标**: 在不改变 Studio / Atelier 核心状态所有权的前提下，构建创作者认证、赛事、培训、商业任务和可审计结算能力。平台账户、支付和商业数据属于新增产品边界，必须经过本方案定义的门禁后再实施。

> **开发状态说明**：本文是开发方案，不是已完成实现证明。当前仓库尚无 `src/apps/platform/`、平台前端目录或平台测试；第 5 节只记录当前仓库可复核的前置检查，不能把历史测试数字当作平台验收结果。

---

## 一、系统全景与业务闭环

### 1.1 平台战略定位
LumenX 原生定位为 AI-Native 视听漫剧生产力工具（Core + Studio + Atelier）。本技术方案在此基础上构建**上层创作者生态与商业变现平台**：

```mermaid
graph TD
    subgraph "1. 创作者孵化链路 (Incubation Loop)"
        User[注册用户] --> Choice{准入路径}
        Choice -->|路径 A| Contest[报名白名单赛事]
        Choice -->|路径 B| Exam[自主申请认证考核]
        Contest --> Academy[参加实战学习培训]
        Academy --> Studio1[LumenX Studio 创作视频]
        Studio1 --> SubmitContest[一键提交赛事作品]
        SubmitContest --> Award[平台评审评奖]
        Award -->|获奖/达标| Certified[成为平台认证创作者]
        Exam --> Studio2[LumenX 制作考核命题]
        Studio2 --> SubmitExam[提交认证审核]
        SubmitExam -->|专家评审通过| Certified
    end

    subgraph "2. 商业众包与履约链路 (Commercial Task Loop)"
        B_End[平台/品牌客户] --> PublishTask[发布商业短剧制作任务]
        PublishTask --> TaskSquare[任务集市 (仅认证创作者可接)]
        Certified --> ClaimTask[接单锁定任务]
        ClaimTask --> Studio3[LumenX 制作商用成片]
        Studio3 --> Deliver[一键交付任务物]
        Deliver --> Review{平台/客户验收}
        Review -->|驳回| Fix[提交修改稿]
        Fix --> Review
        Review -->|合格| Settle[财务结算 / 佣金到账 / 提现]
    end
```

---

## 二、零侵入与向上升级保护架构 (Zero-Intrusion Architecture)

为了降低后续执行 `git merge upstream/main` 时的冲突和污染风险，严格遵循以下隔离原则。物理目录隔离不能保证零冲突；官方装配点仍须通过扩展契约和回归测试保护。

```text
lumenx/
├── src/apps/
│   ├── comic_gen/              # [官方 Core/Studio 内核 - 保持纯净，禁止写业务代码]
│   ├── atelier/                # [官方 Atelier 画布 - 保持纯净]
│   └── platform/               # ★【平台全部业务微服务 - 物理目录 100% 隔离】
│       ├── __init__.py
│       ├── api.py              # 平台总路由聚合 (/api/platform/...)
│       ├── config.py           # 平台独立配置 (JWT Secret、白名单规则等)
│       ├── db.py               # 独立数据库驱动 (SQLite WAL / PostgreSQL)
│       ├── models.py           # 平台 ORM 数据模型
│       ├── auth/               # 认证与用户服务
│       ├── competitions/       # 赛事发布、白名单鉴权、作品评审
│       ├── academy/            # 培训课程与实战模版
│       ├── certs/              # 认证考核与专家打分流转
│       ├── tasks/              # 商业任务集市、抢单认领、交付流转
│       └── settlement/         # 钱包明细、对账、结算打款
├── frontend/src/
│   ├── components/
│   │   ├── modules/            # [官方创作组件 - 保持纯净]
│   │   └── platform/           # ★【平台全部前端界面 - 独立目录】
│   │       ├── auth/           # 登录/注册 AuthModal 弹窗
│   │       ├── competitions/   # 赛事列表、报名审核、榜单
│   │       ├── academy/        # 视频播放器、模板一键 Fork
│   │       ├── certs/          # 创作者考核面板、证书徽章
│   │       ├── tasks/          # 商业任务大厅、交付物上传
│   │       └── wallet/         # 创作者资产明细、提现申请
│   └── store/
│       └── platformStore.ts    # 独立的 Zustand Store，与 projectStore 完全解耦
```

### 2.1 最小代码挂载锚点 (Hook Anchors)
对官方现有代码的侵入严格限制在 **2 个微型挂载点**：
1. **后端装配锚点**：在 [`src/apps/comic_gen/api.py`](file:///Users/aqua/workspace/lumenx/src/apps/comic_gen/api.py) 路由装配处添加 2 行：
   ```python
   # === Platform Extension Hook (Upgrade-Safe) ===
   from ..platform.api import router as platform_router
   target_app.include_router(platform_router, prefix="/api/platform")
   ```
2. **前端导航锚点**：在 [`GlobalSidebar.tsx`](file:///Users/aqua/workspace/lumenx/frontend/src/components/layout/GlobalSidebar.tsx) 中增加 Tab 定义，点击时调用独立的 `<PlatformPortal />` 视图。

### 2.2 数据实体“弱引用”解耦 (Loose Coupling by Reference)
- 平台数据库（`output/platform.db`）与 LumenX 项目数据（`output/projects.json`）**物理隔离**。
- 参赛作品提交表和任务交付表**绝不在官方 `StoryboardFrame` 中注入字段**，仅通过引用记录：
  - `project_id: string`（关联 LumenX 项目 ID）
  - `video_url: string`（指向合成后的成果物如 `output/video/export_xxx.mp4`）
  - `project_title / snapshot: string`（提交时刻的快照信息）

### 2.3 账户与访问边界（新增产品决策门禁）

现有 LumenX 应用访问契约使用部署级访问令牌和应用会话，不包含多人账户或 SaaS 登录。平台新增的用户、创作者、评审员、管理员不能默认复用或覆盖该会话。

开发前必须先采用以下一种明确模式，并记录为独立 ADR：

1. **独立平台服务**：平台账户、数据库和 API 与 LumenX Core 分离，仅通过受控 artifact resolver 读取成果。
2. **同一 FastAPI 的显式 platform mode**：平台路由只在启用平台配置时装配，并使用独立账户会话；现有 `LumenXAccessMiddleware` 仍是部署访问边界。

本方案默认采用第 2 种模式，但在 ADR 未批准前不得编码。平台 JWT 必须定义 secret 来源、有效期、刷新、撤销、轮换、登录限流、密码重置、角色变更审计和 CSRF 防护。`logout` 不能只删除客户端 JWT；必须有服务端会话或撤销机制。

平台路由不得把 `project_id` 或 `video_url` 当作所有权证明。所有提交必须经 Core 的只读 artifact resolver 核验当前用户、环境、项目归属、媒体类型、版本和内容哈希。

---

## 三、数据库设计 (Database Schema)

平台开发存储与正式商业存储分开定义：SQLite 可用于单机开发/试用，正式商业环境默认 PostgreSQL。WAL 只能改善部分读写并发，不能替代事务、锁、迁移、备份和恢复设计。若平台在本地模式运行，数据库文件默认落地于 `output/platform.db`，但不能据此宣称已支持高并发或无缝迁移。

### 3.1 用户与认证表 (`platform_users`)
```sql
CREATE TABLE platform_users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE,
    phone VARCHAR(20) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    salt VARCHAR(64) NOT NULL,
    nickname VARCHAR(50),
    avatar_url VARCHAR(255),
    role VARCHAR(20) DEFAULT 'user',            -- user, creator, reviewer, admin
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_users_username ON platform_users(username);
CREATE INDEX idx_users_email ON platform_users(email);
```

### 3.2 创作者认证档案表 (`creator_certifications`)
```sql
CREATE TABLE creator_certifications (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) UNIQUE REFERENCES platform_users(id),
    status VARCHAR(20) DEFAULT 'unapplied',     -- unapplied, reviewing, certified, rejected
    level VARCHAR(20) DEFAULT 'standard',       -- standard, pro, master
    source_type VARCHAR(20) NOT NULL,           -- 'competition_award' (以赛代考) | 'direct_exam' (自主考核)
    submission_id VARCHAR(36),                  -- 关联的作品或考核记录 ID
    reviewer_id VARCHAR(36) REFERENCES platform_users(id),
    review_score DECIMAL(4, 1),                 -- 评审打分 (0-100)
    review_comment TEXT,                        -- 评审评语
    certified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 赛事活动表 (`competitions`) 与白名单 (`competition_whitelists`)
```sql
CREATE TABLE competitions (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    slug VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    rules_markdown TEXT,
    cover_image_url VARCHAR(255),
    whitelist_required BOOLEAN DEFAULT TRUE,
    allocated_credits INT DEFAULT 500,          -- 白名单参赛者发放的专享算力包
    status VARCHAR(20) DEFAULT 'draft',         -- draft, enrolling, ongoing, judging, ended
    enroll_deadline TIMESTAMP,
    submit_deadline TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE competition_whitelists (
    id VARCHAR(36) PRIMARY KEY,
    competition_id VARCHAR(36) REFERENCES competitions(id),
    user_id VARCHAR(36) REFERENCES platform_users(id),
    invite_code VARCHAR(32),                    -- 邀请码 / 机构邀请通道
    approved_by VARCHAR(36) REFERENCES platform_users(id),
    status VARCHAR(20) DEFAULT 'approved',      -- pending, approved, rejected
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(competition_id, user_id)
);
```

### 3.4 赛事与考核作品提交表 (`platform_submissions`)
```sql
CREATE TABLE platform_submissions (
    id VARCHAR(36) PRIMARY KEY,
    target_type VARCHAR(20) NOT NULL,           -- 'competition' | 'certification_exam'
    target_id VARCHAR(36) NOT NULL,             -- competition_id 或 exam_id
    user_id VARCHAR(36) REFERENCES platform_users(id),
    project_id VARCHAR(36) NOT NULL,            -- 弱引用关联 LumenX Studio 工程 ID
    video_url VARCHAR(255) NOT NULL,            -- 成片文件相对路径
    title VARCHAR(100) NOT NULL,
    description TEXT,
    score DECIMAL(4, 1),                        -- 评审评分
    award_title VARCHAR(50),                    -- 获奖项名称 (一等奖/优胜奖等)
    status VARCHAR(20) DEFAULT 'submitted',     -- submitted, reviewing, approved, rejected
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_subs_user ON platform_submissions(user_id);
CREATE INDEX idx_subs_target ON platform_submissions(target_type, target_id);
```

### 3.5 商业任务广场表 (`commercial_tasks`) 与交付物表 (`task_deliveries`)
```sql
CREATE TABLE commercial_tasks (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(120) NOT NULL,
    category VARCHAR(50) NOT NULL,              -- e.g. 'comic_ad', 'short_drama', 'ip_derivative'
    brief_markdown TEXT NOT NULL,               -- 任务诉求与剧本脚本
    bounty_amount DECIMAL(10, 2) NOT NULL,      -- 酬劳金额 (CNY)
    required_level VARCHAR(20) DEFAULT 'standard', -- 接单最低认证门槛
    claimed_by VARCHAR(36) REFERENCES platform_users(id),
    claim_time TIMESTAMP,
    deadline TIMESTAMP NOT NULL,
    status VARCHAR(20) DEFAULT 'open',          -- open (待认领), locked (已接单制作中), delivered (已交稿), accepted (验收通过), closed (已结算)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE task_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) REFERENCES commercial_tasks(id),
    creator_id VARCHAR(36) REFERENCES platform_users(id),
    project_id VARCHAR(36) NOT NULL,
    video_url VARCHAR(255) NOT NULL,
    version_num INT DEFAULT 1,                  -- 初稿第1版、修改稿第2版
    delivery_note TEXT,
    review_status VARCHAR(20) DEFAULT 'pending',-- pending, revision_required, approved
    review_feedback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.6 钱包与财务对账表 (`creator_wallets` & `settlement_records`)
```sql
CREATE TABLE creator_wallets (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES platform_users(id),
    available_balance DECIMAL(10, 2) DEFAULT 0.00,
    frozen_balance DECIMAL(10, 2) DEFAULT 0.00, -- 提现处理中冻结资金
    total_earned DECIMAL(10, 2) DEFAULT 0.00,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE settlement_records (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) REFERENCES platform_users(id),
    task_id VARCHAR(36) REFERENCES commercial_tasks(id),
    amount DECIMAL(10, 2) NOT NULL,
    type VARCHAR(20) NOT NULL,                  -- 'bounty_income' (任务赏金) | 'withdrawal' (提现)
    status VARCHAR(20) DEFAULT 'settled',       -- pending, settled, paid, failed
    payout_account VARCHAR(100),                -- 支付宝/银行卡账号
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.7 开发规范修订：事实表、约束与迁移

本节优先于 3.1–3.6 中的示例 SQL。示例表不能直接作为生产 schema。

- 使用明确的迁移工具和版本表；每次 schema 变更必须可升级、可回滚或有前向修复脚本。
- SQLite 仅作为单机开发/试用存储；正式商业环境默认 PostgreSQL。若保留 SQLite，必须显式启用 `foreign_keys`、事务、busy timeout、单写者和备份恢复检查。
- 所有状态字段必须有代码枚举和数据库约束；金额使用整数分或明确的 Decimal 适配，不使用浮点。
- 所有写入接口必须支持幂等键，并记录操作者、请求 ID、时间、前后状态和失败原因。
- `platform_submissions` 和 `task_deliveries` 必须保存不可变 artifact snapshot：`artifact_id`、`artifact_version`、`sha256`、`media_type`、`project_title_snapshot`。客户端传入的 URL 只能作为解析输入，不能作为持久化授权依据。
- 课程、模板、模板版本、Fork 记录、评审量表、评审分配、申诉、任务状态历史、通知/outbox 和审计事件必须有独立模型。
- `creator_wallets` 只能作为可重建快照，不能作为资金事实来源。正式结算必须使用不可变账本。

### 3.8 资金账本和提现边界

真实资金功能进入开发前，必须补充以下模型和外部支付契约：

```text
ledger_accounts          账户
ledger_entries           不可变借贷流水
payout_requests          提现申请与幂等键
payout_attempts          渠道尝试、回调和未知状态
reconciliation_batches   对账批次与差异
audit_events             管理和资金操作审计
```

任务验收通过、资金冻结、入账、退款、提现申请和支付回调必须在可重试事务中完成。支付账号必须加密或使用渠道 token；不得明文保存完整银行卡或支付宝敏感信息。若支付渠道、税务和合规边界尚未确定，MVP 只能提供模拟结算流水，不得宣称已支持真实提现。

### 3.9 任务状态机与并发规则

商业任务必须采用显式状态迁移，至少包括：

```text
draft → published → claimed → in_progress → delivered
      → revision_requested → resubmitted → accepted
      → settlement_pending → settled → closed
```

取消、逾期、释放、争议和退款必须有独立迁移。认领使用数据库原子条件（`status = published` + 版本/锁），同一任务只能成功绑定一个创作者；重复请求返回原始认领回执，不得再次扣费或改变归属。每次迁移写入状态历史和审计事件。自动释放由可恢复的后台任务执行，不能依赖进程内定时器。

---

## 四、核心 API 契约设计规范

所有平台 API 统一收敛至 `/api/platform/` 前缀：

### 4.1 认证模块 (`/api/platform/auth`)
- `POST /register`: 用户名、邮箱/手机、密码注册，自动加盐哈希（PBKDF2-SHA256）。
- `POST /login`: 账号密码验证，下发标准 JWT Token。
- `GET /me`: 获取当前用户基本信息、创作者认证状态（是否 Certified）及钱包余额。
- `POST /logout`: 注销当前会话。

### 4.2 赛事与白名单模块 (`/api/platform/competitions`)
- `GET /`: 获取进行中、已结束的赛事列表。
- `GET /{id}`: 赛事详情、竞赛规则与奖励设置。
- `POST /{id}/enroll`: 赛事报名，提供邀请码或申请理由（触发白名单校验）。
- `POST /{id}/submit`: 提交参赛作品（传入 `project_id` 与生成的视频路径，完成挂载）。
- `GET /{id}/leaderboard`: 赛事评奖揭晓结果与获奖展播。

### 4.3 培训与学院模块 (`/api/platform/academy`)
- `GET /courses`: 获取培训教程列表与视频教程。
- `POST /templates/{template_id}/fork`: 将官方教学示范工程一键克隆到当前用户的创作工作台中。

### 4.4 认证考核模块 (`/api/platform/certifications`)
- `GET /exam/prompt`: 获取当前官方认证命题要求。
- `POST /apply`: 提交命题考核成片申请官方认证。
- `GET /status`: 查询认证评审进度与评审专家的评分/评语。

### 4.5 商业任务集市模块 (`/api/platform/tasks`)
- `GET /square`: 任务大厅列表（过滤开放认领、仅对认证创作者开放认领）。
- `POST /{id}/claim`: 创作者认领任务（任务状态流转为 `locked`，绑定到创作者）。
- `POST /{id}/deliver`: 提交阶段成品或最终修改稿。
- `POST /{id}/review`: 【管理员/客户端】验收交付物，提出驳回意见或终审通过。

### 4.6 财务与结算模块 (`/api/platform/settlement`)
- `GET /wallet`: 创作者查看总收入、待结算及可用提现金额。
- `POST /withdraw`: 发起提现申请（支持填写支付宝/银行卡）。
- `GET /transactions`: 查询收支账单流水明细。

### 4.7 API 开发契约（所有模块必须遵守）

每个 endpoint 在实现前必须拥有 Pydantic 请求/响应模型和权限矩阵，明确：

- 身份和角色要求；
- 请求字段、大小限制、时间格式和分页格式；
- 成功响应、错误响应和稳定错误码；
- 幂等键、事务边界和重复请求行为；
- 审计字段、状态迁移和可恢复方式。

文件交付不得直接接受任意本地路径或远程 URL。使用受管理 artifact 上传/解析接口，服务端校验所有权、媒体类型、大小、路径和内容哈希。管理端验收必须与创作者提交权限分离；客户只能访问被授权任务及交付物。

最低权限矩阵：

| 能力 | 用户 | 创作者 | 评审员 | 客户 | 管理员 |
|---|---:|---:|---:|---:|---:|
| 报名/提交自己的作品 | ✓ | ✓ |  |  |  |
| 查看任务广场 |  | ✓ |  | ✓ | ✓ |
| 认领任务 |  | ✓ |  |  |  |
| 评审/请求修改 |  |  | ✓ | ✓ | ✓ |
| 发布/关闭任务 |  |  |  | ✓ | ✓ |
| 结算/提现处理 |  |  |  |  | ✓ |

所有跨用户读取和所有状态写入必须在服务端再次核验，不能依赖前端隐藏按钮。

---

## 五、开发前验证基线 (Pre-Development Baseline)

本节只记录当前仓库可复核的前置检查，不代表平台功能已实现或验收。平台开发必须在独立分支进行，并在首个实现切片提交后重新运行受影响检查。

| 验证项 | 验证命令 | 结果 | 结论 |
| :--- | :--- | :--- | :--- |
| **Git 状态** | `git status --short` | 需以当前提交记录 | 不得把未跟踪方案或历史数字写成 clean |
| **现有 LumenX 文档** | `python3 docs/agents/tools/check_lumenx_refactor_docs.py` | 当前通过 | 仅验证文档结构，不验证平台 |
| **模型目录规范** | `python3 scripts/validate_model_catalog.py` | 当前通过 | 与平台功能无直接等价关系 |
| **平台代码/测试** | `rg --files src frontend/src tests | rg platform` | 已建立无状态契约类型与回归测试 | 仅代表 Gate 0 合同切片，不代表平台就绪 |
| **平台数据库迁移** | 见 [PLATFORM-DATA-CONTRACT.md](PLATFORM-DATA-CONTRACT.md) | 已确定版本表、校验和、SQLite/PostgreSQL 边界 | Sprint 1 实现并验证 |
| **平台 API 契约测试** | `tests/test_platform_contracts.py` | 已建立状态机、artifact 快照、幂等与整数分约束 | 认证/越权/HTTP 路径仍待 Sprint 1 |
| **支付/提现现场验证** | 待定义 | 未定义 | P0：MVP 只能使用模拟结算 |

---

## 六、分阶段敏捷迭代路线图与进入条件 (Milestones)

每个阶段都必须满足前置门禁并提交代码、测试、迁移和证据。原先“6 周完成真实商业闭环”的时间表不再作为承诺。

```text
[Gate 0: 产品与部署边界]
  - 批准平台账户/现有应用访问边界 ADR
  - 确定同体装配或独立服务、SQLite/ PostgreSQL 使用边界
  - 确定支付、税务、作品授权和数据保留范围
  - 验收标准：ADR、权限矩阵、数据分类和回滚策略可审阅。

[Sprint 1: 基础设施 + 认证闭环]
  - 建立平台目录、迁移层、审计事件和测试夹具
  - 实现账户会话、密码策略、角色授权和撤销
  - 实现 artifact ownership resolver，不允许客户端 URL 直接成为证据
  - 实现创作者认证申请、评审、驳回和历史记录
  - 验收标准：认证、越权、会话过期、媒体所有权和重复请求测试通过。

[Sprint 2: 赛事活动 + 白名单规则 + 培训学院]
  - 实现赛事发布、白名单邀请码/初筛机制
  - 赛事专属成片一键提交与挂载
  - 评审打分与榜单评奖，获奖者自动晋升认证创作者
  - 上线学院培训视频列表与官方工程模板一键 Fork
  - 补充课程/模板版本、Fork 归属、评审量表、申诉和审计模型
  - 验收标准：白名单、截止时间、作品快照、评审复核和模板 Fork 全部可恢复。

[Sprint 3: 商业任务广场 + 模拟结算]
  - 商业任务发布后台与众包大厅
  - 认证创作者认领任务、超时自动释放机制
  - 任务成果交付物版本流转与多轮修改验收
  - 实现原子认领、逾期释放、交付版本和多轮验收状态机
  - 使用不可变模拟账本验证入账、冻结、退款和幂等
  - 验收标准：并发抢单、重复交付、重复验收、重启恢复和账本重建测试通过。

[Gate 4: 真实资金放行]
  - 单独批准支付渠道、税务、敏感数据保护和对账方案
  - 完成支付回调签名、未知状态恢复、人工复核和现场验证
  - 未通过 Gate 4 前，禁止开放真实提现和真实资金结算。
```

## 七、开发前阻断清单

- [x] 平台账户与 `LumenXAccessMiddleware` 的边界 ADR 已记录
- [x] 平台 schema、迁移、备份和恢复方案已定义
- [x] artifact ownership resolver 和不可变提交快照已定义
- [x] 任务状态机、原子认领和后台恢复任务已定义
- [x] API 请求/响应/错误/权限/幂等合同已定义
- [ ] 评审、申诉、模板 Fork 和审计模型已补齐
- [x] 不可变模拟账本合同已定义（实现与测试在 Sprint 3）
- [x] 隐私、版权、支付账号和数据删除边界已记录
- [x] 当前提交、责任文件和实际验证命令已记录
