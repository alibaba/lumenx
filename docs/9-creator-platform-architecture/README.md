# 9. LumenX 创作者生态与商业任务平台技术架构

本目录收录 LumenX 创作者生态商业化平台（注册登录、白名单赛事、实战培训、认证考核、商业任务众包、结算对账）的开发方案与设计规范。当前方案已完成终审修订，但仍处于开发前门禁阶段，不表示平台代码或真实结算已经实现。

## 目录索引

- [TECH-SPEC.md](TECH-SPEC.md) - 完整技术架构方案规范与数据库、API、无侵入扩展设计
- [PLATFORM-DATA-CONTRACT.md](PLATFORM-DATA-CONTRACT.md) - Gate 0 的访问、迁移、artifact、状态机、API 和模拟账本合同
- [PLATFORM-TEST-MATRIX.md](PLATFORM-TEST-MATRIX.md) - 平台新增合同与后续 Sprint 的测试映射
- [adr/0001-platform-access-boundary.md](adr/0001-platform-access-boundary.md) - 平台账户与现有部署访问令牌的边界决策

## 核心设计原则

1. **隔离与向上升级保护**：平台业务代码放在 `src/apps/platform/` 与 `frontend/src/components/platform/`，并通过显式装配契约、feature flag 和回归测试降低上游合并风险；物理隔离本身不保证零冲突。
2. **弱引用实体关联**：商业业务表（赛事提交、任务交付）仅通过 `project_id` 和成果 `video_url` 关联 LumenX 项目，不污染官方分镜与项目数据结构。
3. **独立持久化引擎**：平台数据与 Studio/Atelier 状态物理隔离；SQLite 仅用于单机开发/试用，正式商业环境默认 PostgreSQL。迁移、备份、恢复和资金账本必须先通过开发门禁。

## 当前执行切片

2026-10-31 已在独立分支 `platform-gate0` 完成 Gate 0 合同与无状态 Python 契约类型的第一切片：

- 平台默认关闭，平台会话与 `LumenXAccessMiddleware` 分离；
- artifact 提交必须保存版本、哈希、媒体类型和项目标题快照；
- 任务状态迁移、幂等上下文和整数分模拟账本已形成可执行 Pydantic 合同；
- 平台路由已受 `LUMENX_PLATFORM_ENABLED` 控制；认证、赛事白名单、培训/模板、认证考核、商业任务和模拟账本业务已实现。
- 真实支付、提现渠道和外部回调按当前要求跳过；接受任务只写入模拟不可变账本。
- 前端平台入口已接入 `#/platform`，目录为 `frontend/src/components/platform/`，账户 API/store 分别位于 `frontend/src/lib/platformApi.ts` 与 `frontend/src/store/platformStore.ts`。

对应文件：`src/apps/platform/` 与 `tests/test_platform_*.py`。

## 当前开发门禁

开发前必须先完成 `TECH-SPEC.md` 第 2.3、3.7–3.9、4.7 和第 7 节。特别是账户边界、artifact 所有权、任务状态机、不可变账本、API 幂等和隐私合规未完成前，不得进入真实提现或商业任务生产运行。
