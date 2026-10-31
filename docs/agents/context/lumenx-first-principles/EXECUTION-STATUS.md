# LumenX 公开候选验证台账

整理日期：2026-10-30。此文件保留公开发布的来源边界与逐候选验证责任，不复制会随提交变化的 CI 结果，也不替代或改写原研发分支的完整台账。产品决定、实施依赖与反例分别见 [契约](PRODUCT-CONTRACT.md)、[计划](REFACTOR-PLAN.md) 和 [测试矩阵](TEST-MATRIX.md)。

## 固定来源与公开结果

- 公开基线：`f2a02e23171447c939e7d8e1386b24d17049bbf1`。
- 最终研发来源：`7765f9e816088a88f8489a737ad70eb7211de051`。
- 公开候选保留最终实现和回归测试，另行整理文档、依赖安装和发布检查；这些差异需要在实际候选上验证。
- 当前结果的核对方法见 [发布准备记录](../../deliverables/github-release-2026-10-30/README.md)：使用关联 PR 的同提交 Checks/CI artifacts，以及注明源码版本的本机补充结果。下表“需复核”是每个候选的固定验证义务，不是“尚未运行”或“失败”结论，也不表示原研发修复被撤销。

来源台账记录过：后端 `c1197b6f` 9,460 项通过；前端 `013897a3` 2,104 单元与 1,844 界面测试通过，类型检查和构建通过；两提交之间只变更两份测试，运行代码相同。来源还记录了四条代表浏览器旅程、ECS 回环 API/媒体/正常重启、一次本机→ECS→本机工程往返及 macOS 包结构检查。这些原始证据**未随公开候选发布**，上述数字不是本分支新运行结果，也不能用于宣称所有功能或所有环境已验收。

原前端 lint 有 416 条历史诊断且退出码为 1；保留债务与无新增比较，不能把它写成原始 lint 通过。公开分支必须记录自身检查结果。

## 切片与逐候选验证责任

| 切片 | 验证要求 | 验证范围 | 结果核对方法 |
|---|---|---|---|
| P0 | 需复核 | 公开来源、工作区与材料边界 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P1 | 需复核 | 收集前隔离、应用组装、数据/媒体路径 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P2M | 需复核 | Studio/Playground 保存、版本、共享读回与文档状态 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P2A | 需复核 | Atelier 独立项目、画布、上传、序列和状态所有权 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P3 | 需复核 | 共同参数、能力子集、固定媒体和批准绑定 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P4 | 需复核 | Agent 接纳、权限、工具与四类 HTTP 路径 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P5 | 需复核 | 供应商路由、单次提交与结果合同 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P6 | 需复核 | 原任务身份、查询/取回/修复与界面恢复 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P7 | 需复核 | 服务身份、访问控制、环境隔离与桌面生命周期 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P8 | 需复核 | 独立工程复制、媒体闭包与执行权清理 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P9 | 需复核 | 整合回归、前端构建与代表性浏览器旅程 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |
| P10 | 待现场 | 正式桌面产物及浏览器/桌面 × 本地/远程现场验收 | [候选检查](../../deliverables/github-release-2026-10-30/README.md) |

## 剩余现场范围

- 正式 macOS 应用冷启动、关闭/重开、本地/远程模式与媒体操作须在允许执行该正式包的环境验证；来源的包检查不能替代启动验收。签名、公证和 DMG、Windows 安装/运行分别记录。
- 公开 HTTPS、真实浏览器登录会话、媒体 Range、桌面连接远程服务与四种入口组合仍须现场验证。服务器回环 API 成功不能替代公网浏览器。
- 真实模型的提交、收费、恢复和输出质量须按明确模型/账号/输入范围验证；离线传输替身和可解码媒体不代表真实供应商通过。
- 候选集成结果不自动涵盖其它未合入分支、所有模型组合或异常断电。未新增失败时不重复扩大验证范围。

## 历史发现到规范的映射

[历史发现摘要](HISTORICAL-FINDINGS.md)保留 ARCH/CORE 定义；[旅程](PRODUCT-JOURNEYS.md)保留 S-R/A-R 反例。下面仅说明验证归属，不继承旧证据的通过状态。

| 发现 | 负责切片 | 功能与场景 | 公开候选判断 |
|---|---|---|---|
| ARCH:R1 Studio 保存 | P2M | F01/F06/F44；CJ-05 | 按对应行为复核；原处置记录未公开 |
| ARCH:R2 网络访问边界 | P7 | F19/F21/F43；CJ-06/08 | 按对应行为复核；原处置记录未公开 |
| ARCH:R3 Tauri 生命周期 | P7/P10 | F23；CJ-06 | 按对应行为复核；原处置记录未公开 |
| ARCH:R4 节点字段覆盖 | P2A | F25/F27/F28；CJ-05 | 按对应行为复核；原处置记录未公开 |
| ARCH:R5 单轮 SSE 派发 | P4 | F35/F38；AJ-03/CJ-02 | 按对应行为复核；原处置记录未公开 |
| ARCH:R6 入口能力差异 | P3/P4 | F11–14/F30/F36/F37；CJ-01 | 按对应行为复核；原处置记录未公开 |
| ARCH:R7 Wan 音频语义 | P3/P5 | F12/F30；CJ-01 | 按对应行为复核；原处置记录未公开 |
| ARCH:R8 workspace 配置 | P3/P7 | F21/F43；CJ-01/06 | 按对应行为复核；原处置记录未公开 |
| ARCH:R9 资产任务恢复 | P6 | F04/F15/F35；CJ-04 | 按对应行为复核；原处置记录未公开 |
| ARCH:R10 单写者 | P2M/P2A | F44；CJ-05 | 按对应行为复核；原处置记录未公开 |
| ARCH:R11 测试门禁 | P1/P9/P10 | F01–F44 | 按对应行为复核；原处置记录未公开 |
| CORE:C1 请求外形 | P3 | F11/F30；CJ-01 | 按对应行为复核；原处置记录未公开 |
| CORE:C2 Agent 字段落后 | P3/P4 | F30/F36/F37；AJ-03/CJ-01 | 按对应行为复核；原处置记录未公开 |
| CORE:C3 接纳结果派发 | P4 | F35/F38；AJ-03/CJ-02 | 按对应行为复核；原处置记录未公开 |
| CORE:C4 默认/校验分叉 | P3/P5 | F11–14/F30/F37；CJ-01/03 | 按对应行为复核；原处置记录未公开 |
| CORE:C5 分支同名契约漂移 | P0/P5/P9 | F11/F12/F20/F22 | 按对应行为复核；原处置记录未公开 |
| S-R1 原文未落盘 | P2M/P9 | F01；SJ-01 | 按对应行为复核；原处置记录未公开 |
| S-R2 共享角色声音 | P2M/P9 | F04/F09/F17；SJ-02 | 按对应行为复核；原处置记录未公开 |
| S-R3 配音套错 take | P2M/P9 | F16–18；SJ-05 | 按对应行为复核；原处置记录未公开 |
| S-R4 混音不生效 | P9 | F17/F18；SJ-06 | 按对应行为复核；原处置记录未公开 |
| S-R5 成片缺项/过时 | P9 | F18/F19；SJ-07/08 | 按对应行为复核；原处置记录未公开 |
| A-R1 保存/导出脱节 | P2A/P9 | F40/F41；AJ-06 | 按对应行为复核；原处置记录未公开 |
| A-R2 隐藏失效条目 | P9 | F28/F33/F40/F41；AJ-07 | 按对应行为复核；原处置记录未公开 |
| A-R3 音轨 UI 子集 | P3/P9 | F41；CJ-01 | 按对应行为复核；原处置记录未公开 |
| A-R4 导出中断/Redo 反馈 | P6/P9 | F41；AJ-08 | 按对应行为复核；原处置记录未公开 |

## 记录规则

每次验证保留固定提交或源码清单、工具版本、实际命令、收集/通过/失败/跳过数量、外部替身和临时数据范围。运行失败与修复后的结果分别保留；构建、浏览器、安装包和真实模型分别判断。动态结果放在对应 PR Checks/CI artifacts 及注明版本的补充记录中；修改运行代码后，受影响结论重新验证。不要为复制一次运行数字而反复修改本文件，也不要让文档结构检查关闭产品验收。

## 2026-10-31 创作者平台 Gate 0 切片

平台方案位于 `docs/9-creator-platform-architecture/`，当前从 Gate 0 开始执行。首个切片在独立分支 `platform-gate0` 建立了平台访问边界 ADR、数据/API 合同和无状态 Pydantic 契约类型；没有装配平台路由、创建平台数据库或开放真实支付。

- 责任文件：`docs/9-creator-platform-architecture/adr/0001-platform-access-boundary.md`、`PLATFORM-DATA-CONTRACT.md`、`PLATFORM-TEST-MATRIX.md`、`src/apps/platform/contracts.py`、`src/apps/platform/config.py`、`src/apps/platform/db.py`、`src/apps/platform/migrations/001_initial.sql`、`tests/test_platform_contracts.py`、`tests/test_platform_db.py`。
- 验证：`.venv/bin/python -S scripts/run_tests_isolated.py -- tests/test_platform_contracts.py tests/test_platform_db.py -q`（6 passed）；`python3 docs/agents/tools/check_lumenx_refactor_docs.py`（PASS）。
- 证据边界：依赖安装使用隔离 `.venv`；结果覆盖无状态合同与 SQLite 迁移/备份基础，不代表认证、HTTP、artifact resolver 或商业结算已通过。

## 2026-10-31 创作者平台业务闭环切片

按用户要求跳过真实支付，继续实现平台业务逻辑。`src/apps/platform/service.py` 和 `api.py` 已提供账户会话、赛事白名单、作品快照提交、课程/模板 Fork、认证考核、任务认领/开工/交付/评审、模拟账本和审计；平台路由仅在 `LUMENX_PLATFORM_ENABLED=true` 时装配。

- 迁移：`002_business.sql` 建立赛事、提交、学院、认证、任务、交付、状态历史、幂等和模拟账本表。
- 验证：`.venv/bin/python -S scripts/run_tests_isolated.py -- tests/test_platform_contracts.py tests/test_platform_db.py tests/test_platform_service.py -q`（9 passed）。
- 证据边界：支付渠道、提现、Core 实际 artifact resolver 和生产部署尚未验收；当前提交接口只保存客户端传入快照，后续必须替换为只读 resolver。
- 补充入口：赛事详情/榜单/评审、认证命题读取、模拟账单查询、任务截止释放已加入平台 router；真实支付与提现仍跳过。

## 2026-10-31 创作者平台统一身份与算力账本切片

平台会话现在可作为 Studio/Atelier Core 请求的 Bearer 身份传递；远程 `AppAccess` 也接受已验证的平台 Bearer 会话。未登录进入 Studio/Atelier 时，前端回到 `#/platform` 登录入口，平台页面保持公开以便完成登录。

- 算力记录：`src/core/token_usage.py` 提供请求级记录钩子；`src/apps/platform/migrations/003_token_usage.sql` 建立独立 token 使用账本，按用户、产品、操作、供应商、模型、输入/输出/总 token 记录。
- 查询入口：`GET /api/platform/usage/tokens`；平台工作台显示当前账户累计 token，并提供进入 LumenX 工作区的入口。
- 请求接线：`frontend/src/lib/appAccess.ts` 传递平台 Bearer token，Studio/Atelier 分别发送 `X-LumenX-Product`；`LLMAdapter` 将实际 provider usage 写入平台账本。
- 验证：平台测试覆盖迁移、用户隔离和算力汇总；真实供应商调用仍未执行，支付和提现仍跳过。

## 2026-10-31 平台积分算力账本切片

在 token 使用记录之上增加平台积分价格、积分账本和生成预扣闭环。当前充值只提供管理员/客户模拟发放，真实支付仍跳过。

- 迁移：`004_credit_ledger.sql` 建立 `credit_pricing`、`credit_ledger`、`credit_reservations`，并写入 DashScope Qwen 默认价格版本。
- 服务：支持价格估算、余额查询、模拟积分发放、预扣、实际结算、差额释放、失败释放和幂等重放。
- API：`/api/platform/credits/pricing`、`estimate`、`balance`、`ledger`、`grants`、`reservations` 及 settle/release 入口已加入；前端 `platformApi` 已提供对应客户端方法。
- 计费边界：本切片只完成平台积分账本，不改变 Provider 调用凭证；用户 BYOK 凭证加密存储和 resolver 将在下一切片实现。
- 验证：平台合同、数据库、服务测试 11 项通过；文档结构检查通过。

## 2026-10-31 BYOK 用户凭证切片

平台开始支持用户自带 Provider API-Key（BYOK）。凭证按平台用户隔离，使用 AES-GCM 加密保存，前端只接收脱敏元数据；请求通过 `credential_ref` 选择凭证，后端在请求上下文中临时解密并注入 LLM Adapter。

- 迁移：`005_user_credentials.sql` 建立用户凭证表，保存密文、nonce、指纹、状态和验证时间，不保存明文 Key。
- API：`/api/platform/credentials` 支持创建、列表、验证、轮换和撤销；验证当前是本地解密/格式检查，尚未对真实 Provider 发起验证调用。
- 请求合同：`X-LumenX-Billing-Mode: byok` 与 `X-LumenX-Credential-Ref` 必须成对出现；平台积分模式携带凭证引用会被拒绝。
- 加密配置：`LUMENX_PLATFORM_CREDENTIALS_KEY` 必须是可解码为 32 字节的 URL-safe Base64 密钥；未配置时 BYOK 返回 `credentials_key_required`，不会回退到平台 Key。
- 验证：平台、AppAccess 和凭证隔离测试共 39 项通过；真实 Provider 调用和真实密钥验证仍未执行。

## 2026-10-31 算力来源前端入口切片

平台工作台新增“算力与凭证”模块，用户可在平台积分和 BYOK 之间选择，创建/查看/撤销脱敏凭证。Studio 的 `appAccessFetch` 与 Axios 访问会读取该选择并发送 `X-LumenX-Billing-Mode`、`X-LumenX-Credential-Ref`；Atelier 保持独立产品标识并复用同一算力来源。

- 新增 `frontend/src/components/platform/ComputeSettings.tsx` 和 `frontend/src/lib/computePreference.ts`。
- 平台客户端已接入凭证管理、积分余额、价格估算、预扣、结算和释放方法。
- 证据边界：当前生成入口已传递计费模式，但 Studio/Atelier 的各类 Provider 仍需逐一接入实际预扣/结算；本切片未执行真实 Provider 请求。
- 验证：前端 typecheck 与 production build 通过。

## 2026-10-31 LLM 积分预扣与实际结算切片

LLM Adapter 现在读取请求级算力计费上下文。平台积分模式在 Provider 调用前按估算 token 预扣，返回 usage 后按实际 token 结算，异常释放预扣；BYOK 模式不触发积分账本，只保留 token usage 记录。

- Core 新增 `src/core/compute_billing.py`，平台服务提供 `reserve_for_usage` 与 `settle_for_usage` 回调。
- `LLMAdapter.chat` 和 `chat_result` 已接入预扣/结算/释放路径；没有平台上下文的旧调用保持原行为。
- 验证：LLM bounded/environment、平台合同/迁移/服务测试 47 项通过；真实 Provider、图像/视频/语音计费仍待接入。

## 2026-10-31 媒体算力价格目录切片

为后续图片、视频和音频生成接入持久化预扣/结算，平台新增按媒体单位计价的独立目录。该切片先完成价格原语和查询入口，不宣称媒体生成已经完成计费接线。

- 迁移：`006_media_pricing.sql` 建立 `media_pricing`，按 provider、model、media_kind 和 pricing_version 保存每单位积分价格；当前种子覆盖 DashScope 图片、Kling 视频和 CosyVoice 音频默认价格。
- 服务/API：新增媒体价格列表和单位估算方法，未知具体模型回退到 provider 的 `*` 默认价格；前端 `platformApi` 已提供查询客户端。
- 验证：平台数据库/服务测试 10 项通过，前端 typecheck 通过。
- 证据边界：图片、视频、音频后台任务仍需把 reservation id 写入持久回执，并在完成/失败/恢复路径结算或释放；真实 Provider 和价格校准尚未执行。

## 2026-10-31 Product Skill 作用域解析基础切片

按开发更新方案开始实现 Skill runtime 基础合同。新增 `src/apps/atelier/skill_resolution.py`，在不改变现有 Agent wire contract 的前提下，提供 system/workspace/user/project/session 作用域优先级、同级冲突拒绝、当前入口工具交集和不可变有效 Skill 视图。该切片不读取外部 Skill 路径、不执行脚本、不持久化用户覆盖，也不开放多用户 Skill API。

- 责任文件：`src/apps/atelier/skill_resolution.py`、`tests/test_atelier_skill_resolution.py`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：隔离 runner 执行 `tests/test_atelier_skill_resolution.py tests/test_atelier_product_skills.py -q`（5 passed）；`python3 docs/agents/tools/check_lumenx_refactor_docs.py`（PASS）。
- 证据边界：当前 Product Skill 仍是静态系统注册表；用户/项目覆盖的存储、API、UI、Skill sandbox、运行快照持久化和媒体计费接线尚未实现。

## 2026-10-31 Product Skill 版本与绑定持久化切片

继续按 Skill 云端开发方案实现平台侧持久化合同。新增 `007_skill_registry.sql`、`PlatformService.create_skill_version`、`bind_skill_version` 和 `resolve_skill_version`，支持 system/workspace/user/project 绑定优先级、用户隔离、项目覆盖、manifest 内容哈希和可执行/凭证字段拒绝。当前只保存受控 JSON manifest，不把外部 Skill 文件或脚本带入运行时。

- 责任文件：`src/apps/platform/migrations/007_skill_registry.sql`、`src/apps/platform/service.py`、`tests/test_platform_db.py`、`tests/test_platform_service.py`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：隔离 runner 执行平台合同、数据库、服务测试（15 passed）。
- 证据边界：尚未接 HTTP/API、前端编辑、Atelier snapshot converter、Skill sandbox、反馈优化或多用户 worker。
