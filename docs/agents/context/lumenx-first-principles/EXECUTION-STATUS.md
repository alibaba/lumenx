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
