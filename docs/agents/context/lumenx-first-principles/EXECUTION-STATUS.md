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

## 2026-10-31 Product Skill API 与前端绑定切片

继续完成 Skill 纵向接线。`src/apps/platform/api.py` 新增 Skill 版本列表、创建、绑定和解析入口；`frontend/src/lib/platformApi.ts` 与 `frontend/src/components/platform/SkillSettings.tsx` 接入账户级/项目级 Skill 管理入口。客户端在提交前按服务端同一 canonical JSON 规则计算 SHA-256；服务端仍是 manifest 和权限的最终校验者。

- 责任文件：`src/apps/platform/api.py`、`frontend/src/lib/platformApi.ts`、`frontend/src/components/platform/SkillSettings.tsx`、`frontend/src/components/platform/PlatformPortal.tsx`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：平台隔离测试（15 passed）；`npm --prefix frontend run typecheck`（通过）；`npm --prefix frontend run build`（通过，保留 Next 静态导出 rewrites 警告）；文档结构检查（PASS）。
- 证据边界：尚未完成 Atelier snapshot converter、Skill sandbox、用户反馈优化、视频生成接纳和多用户 worker。

## 2026-10-31 Skill converter、静态沙箱与视频接纳绑定切片

新增 `src/apps/atelier/skill_runtime.py`，完成平台 manifest 到 `AtelierProductSkillSnapshot` 的严格转换、禁止脚本/路径/网络/凭证字段的静态沙箱校验，以及带 Skill 内容哈希的视频请求绑定。`AtelierGenerationAdmissionService.admit` 可接收 Skill snapshot；绑定哈希进入 request fingerprint 和 candidate projection，重放必须使用同一 Skill 快照。

- 责任文件：`src/apps/atelier/skill_runtime.py`、`src/apps/atelier/generation_admission.py`、`src/apps/atelier/generation_ledger.py`、`tests/test_atelier_skill_runtime.py`、`tests/test_atelier_generation_admission.py`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：Skill/runtime、generation admission、scope resolution、product Skill 测试（37 passed）。
- 证据边界：这是静态沙箱和单次接纳绑定，不是隔离 worker；Skill snapshot 尚未单独写入 generation attempt schema，反馈优化和多用户 worker 仍未实现。

## 2026-10-31 Skill 运行快照持久化与 Atelier 运行时桥接切片

生成 attempt 现在保存 `SkillExecutionBinding`，candidate projection、request fingerprint 和 attempt readback 使用同一 Skill 内容哈希。`AtelierAgentLoopService` 支持注入平台 manifest resolver：有已授权平台 manifest 时先转换为 Atelier snapshot，没有时回退到系统 Product Skill registry；Skill 名称不一致会拒绝。该桥接不让 Atelier 直接依赖平台数据库。

- 责任文件：`src/apps/atelier/models.py`、`src/apps/atelier/generation_admission.py`、`src/apps/atelier/agent_admission.py`、`src/apps/atelier/skill_runtime.py`、`src/apps/atelier/agent_loop.py`、相关测试。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：Atelier Skill/runtime、generation admission、agent loop storage、scope/product Skill 测试（55 passed）；平台相关 54 项测试仍保持通过；前端未改运行代码，typecheck/build 基线保持通过。
- 证据边界：平台 manifest resolver 仍由应用组合根注入；尚未实现平台服务到 Atelier 的真实请求上下文接线、隔离 worker、队列恢复和反馈优化。

## 2026-10-31 真实请求上下文桥接与隔离 Worker 合同切片

Atelier Agent loop HTTP 入口现在从应用组合根的 platform middleware 获取认证 actor，按 `skill_name + project_id` 调用平台 Skill resolver，将受控 manifest 转为 Atelier snapshot 后启动 loop；未认证或无绑定时回退系统 registry。新增 `skill_worker_contract.py` 定义未来隔离 worker 的无网络、无凭证、无脚本 job 合同，并通过测试验证禁止字段和 Skill binding。

- 责任文件：`src/apps/atelier/agent_loop.py`、`src/apps/atelier/agent_loop_api.py`、`src/apps/atelier/skill_worker_contract.py`、`tests/test_atelier_skill_runtime.py`、`tests/test_atelier_skill_worker_contract.py`、`docs/operations/skill-worker.md`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：新增桥接/worker 测试与 Agent storage/generation tests（53 passed）；平台 54 项测试保持通过；前端 typecheck 基线保持通过。
- 证据边界：当前只完成 API 请求上下文桥接和 worker 消息合同；未启动队列、隔离容器、独立凭证代理或多用户生产 worker。

## 2026-10-31 Skill run snapshot ledger 切片

新增 `008_skill_runs.sql`、平台运行账本服务和 `/api/platform/skills/runs` 入口。运行记录按用户与 request fingerprint 幂等，保存项目、Skill 版本/内容哈希、输入/输出哈希、状态和错误；用户隔离更新已测试。该账本为后续 Worker claim、租约和重启恢复提供事实源。

- 责任文件：`src/apps/platform/migrations/008_skill_runs.sql`、`src/apps/platform/service.py`、`src/apps/platform/api.py`、平台数据库/服务测试。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：平台 Skill 迁移、服务、API 和 Atelier 相关测试将在本切片完成后统一运行。
- 证据边界：尚未实现 queued→running 原子 claim、租约、独立 Worker、队列恢复或多用户生产调度。

## 2026-10-31 Skill run atomic claim 与租约恢复切片

新增 `009_skill_run_leases.sql`，为 Skill run ledger 增加 `lease_owner`、`lease_expires_at` 和 `attempt_count`。平台服务支持原子 claim、活跃租约冲突、过期回收和带 worker 身份的完成更新；API 增加 claim/recover 入口。该切片先验证单写者事务和恢复语义，尚未接独立队列或容器 Worker。

- 责任文件：`src/apps/platform/migrations/009_skill_run_leases.sql`、`src/apps/platform/service.py`、`src/apps/platform/api.py`、平台数据库/服务测试。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：平台 Skill 运行账本、claim、租约和恢复测试将在本切片完成后统一运行。
- 证据边界：当前 Worker 身份是受控 API 字段，尚未实现独立 Worker 凭证、队列租约心跳或跨进程生产恢复。

## 2026-10-31 Worker identity、heartbeat 与跨进程恢复切片

新增 `010_skill_workers.sql` 和按用户隔离的 Worker identity token 哈希。Skill run 支持 token claim、heartbeat、租约续期和跨进程 SQLite 账本竞争；两个 `PlatformService` 实例的恢复测试覆盖 Worker token、heartbeat 和用户边界。该切片仍是持久化队列合同，不是 Redis/PostgreSQL 集群。

- 责任文件：`src/apps/platform/migrations/010_skill_workers.sql`、`src/apps/platform/service.py`、`src/apps/platform/api.py`、平台数据库/服务测试。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：Worker identity、claim、heartbeat、跨进程恢复和平台 Skill 相关测试将在本切片完成后统一运行。
- 证据边界：尚未实现集中式队列、独立 Worker 凭证代理、Redis/PostgreSQL job ledger 或容器级隔离。

## 2026-10-31 Redis/PostgreSQL 队列配置与 Worker 隔离配方切片

新增 `queue_backend.py`，显式支持 sqlite/redis/postgres 队列后端配置校验；新增 `Dockerfile.skill-worker` 和 Compose `skill-production` profile，提供只读文件系统、capabilities drop、no-new-privileges 和 noexec tmpfs 的 Worker 容器边界。该切片验证配置和安全配方，不宣称 Redis/PostgreSQL 已连接或真实 Worker 已提交 Provider 任务。

- 责任文件：`src/apps/platform/queue_backend.py`、`tests/test_skill_queue_backend.py`、`Dockerfile.skill-worker`、`docker-compose.yml`、`docs/operations/skill-worker.md`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：队列配置单测、平台 Skill/Worker 测试、文档检查和前端 typecheck；真实 Redis/PostgreSQL 现场连接未执行。
- 证据边界：Redis/PostgreSQL adapter、集中式 job ledger、Worker claim 监控和 Provider 执行仍待生产现场验收。

## 2026-10-31 集中队列适配器与隔离 Worker runner 切片

新增 `central_queue.py`：Redis Streams 和 PostgreSQL `FOR UPDATE SKIP LOCKED` 适配器共享统一 enqueue/claim/ack/requeue 合同；新增 `IsolatedSkillWorker` runner 和 Fake backend 回归测试。`requirements-docker.txt` 增加 redis/psycopg 运行依赖，容器配方仍使用受限权限和只读根文件系统。

- 责任文件：`src/apps/platform/central_queue.py`、`tests/test_central_skill_queue.py`、`requirements-docker.txt`、`Dockerfile.skill-worker`、队列/Worker 文档。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：Fake queue runner、队列配置、平台 Skill/Worker 测试、Compose config 和文档检查；未连接真实 Redis/PostgreSQL。
- 证据边界：适配器代码已具备真实连接入口，但 live Redis/PostgreSQL、Provider handler、监控和容器部署仍需环境现场验收。

## 2026-10-31 真实 Redis 现场与 Worker 容器 smoke 切片

新增最小 `skill-worker` 入口和 Worker 专用依赖文件，容器只复制队列适配器与入口，不带 API、模型目录或用户数据。使用 Compose project `lumenx-live` 启动 Redis 7 和 PostgreSQL 16；Redis Streams 写入 `live-redis-2` 后由容器 Worker 消费并 ack，`XPENDING` 为 `0`。修复 Redis block timeout 被客户端抛出后导致 Worker 退出的问题，将其归一为空闲轮询。

- 责任文件：`src/apps/platform/skill_worker_main.py`、`requirements-worker.txt`、`Dockerfile.skill-worker`、`docker-compose.yml`、`src/apps/platform/central_queue.py`、`tests/test_skill_worker_main.py`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：Redis 容器到 Worker 的真实消费/ack smoke 通过；PostgreSQL 16 容器健康检查通过；队列、平台、Worker 合同测试与 Compose config 待本切片统一回归。
- 证据边界：PostgreSQL adapter 的真实 enqueue/claim/ack 尚未执行；Worker 仍为 dry-run handler，不调用 Provider；尚未完成多用户压力、监控、凭证代理和生产发布门禁。

## 2026-11-01 PostgreSQL 现场并发、Worker 监控与凭证代理边界切片

通过 loopback PostgreSQL 16 现场运行 `scripts/verify_live_skill_queue.py`：24 个任务由 6 个并发 Worker 完成 enqueue → claim → ack，结果为 `claimed=24`、`unique=24`、`duplicates=0`。新增 `worker_runtime.py`，为 Worker 提供无 payload 的结构化计数快照和凭证代理客户端；Provider job 未配置代理时 fail-closed，配置代理时只接收 opaque `credential_ref`，不会把 secret 放入 job 或日志。

- 责任文件：`src/apps/platform/worker_runtime.py`、`src/apps/platform/skill_worker_main.py`、`scripts/verify_live_skill_queue.py`、`tests/test_worker_runtime.py`、`tests/test_skill_worker_main.py`、`docker-compose.yml`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：PostgreSQL 真实 24-job/6-worker smoke 通过；Worker/队列/平台/Atelier 测试与 Compose 配置待统一回归；凭证代理为受控 HTTP 边界测试，未连接真实 Provider。
- 证据边界：当前监控是 Worker 结构化日志，不是 Prometheus/OTel 集群；凭证代理客户端已接入 fail-closed 合同，但代理服务、密钥轮换、审计与真实 Provider 端到端仍待生产接入。

## 2026-11-01 真实凭证代理、Prometheus/OTel 与多用户生产 profile 验收切片

新增独立 `credential-proxy` 服务：Worker 只能 issue opaque `credential_ref`，Core 使用独立 bearer token resolve，引用默认 300 秒过期，未配置或未知 provider 时 fail-closed。Worker 新增 `/metrics` Prometheus 文本端点；Compose `monitoring` profile 启动 Prometheus 与 OTel Collector，二者通过容器网络抓取 Worker 指标。

- 责任文件：`src/apps/platform/credential_proxy_service.py`、`src/apps/platform/worker_runtime.py`、`Dockerfile.credential-proxy`、`requirements-credential-proxy.txt`、`deploy/prometheus/prometheus.yml`、`deploy/otel/otel-collector.yml`、`docker-compose.yml`、`scripts/verify_live_skill_queue.py`。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：凭证代理 health/issue/resolve 真实容器 smoke 通过；Prometheus target 为 `up`；OTel Collector debug exporter 收到 Worker 指标；PostgreSQL 240-job/12-worker/24-tenant 压力通过，0 duplicate、每租户 10 个。
- 证据边界：当前代理的本地配置适配器从环境变量读取 secret，生产云部署必须替换为 Secret Manager/KMS；OTel 当前使用 debug exporter，告警、持久化远端 exporter、HA 和故障演练仍待接入。

## 2026-11-01 生产门禁、告警规则与远端 OTLP 模板切片

凭证代理增加 `SecretProvider` 抽象和 `/readyz`：缺少 Worker/Core token 时拒绝 ready；本地环境适配器仍明确标记为 Secret Manager/KMS 的替换点。新增 `verify_skill_production.py`，检查代理 readiness、Worker Prometheus 指标、Prometheus target 和 OTel Collector 可达性；云端必须提供非默认 token，不能使用 `--allow-local`。Prometheus 增加 Worker down/失败率告警，OTel 增加远端 OTLP HTTP 配置模板。

- 责任文件：`src/apps/platform/credential_proxy_service.py`、`scripts/verify_skill_production.py`、`deploy/prometheus/alerts.yml`、`deploy/otel/otel-collector-remote.yml`、相关测试与文档。
- 分支/提交：`platform-gate0` / 待提交（本切片完成后记录固定提交）。
- 验证：`scripts/verify_skill_production.py --allow-local` 返回 proxy ready、Worker metrics up、Prometheus target up、OTel reachable；凭证代理 readiness、监控配置和 42 项 Skill/平台/Atelier 测试通过。
- 证据边界：SecretProvider 真实云厂商实现、远端 OTLP 账号、告警通知渠道、云端 HA/弹性和故障演练仍需部署参数与现场执行。
