---
status: accepted
date: 2026-10-31
decision_source: TECH-SPEC.md default deployment mode and user request to begin execution
---

# 平台采用显式 platform mode 与独立平台会话

## 决策

平台先作为同一 FastAPI 应用中的显式扩展装配，但必须满足以下边界：

- `LUMENX_PLATFORM_ENABLED` 默认为 `false`。未启用时不装配平台路由，也不创建平台数据库、会话或后台任务。
- 平台 API 统一挂载在 `/api/platform`，使用独立账户会话；不得把平台 JWT 当作 `LumenXAccessMiddleware` 的部署访问令牌。
- 平台会话使用服务端可撤销 session 记录；JWT 只携带不可变 session id 和过期时间。登出、撤销、密码重置和角色变更都写审计事件。
- 平台数据库默认是 `output/platform.db` 的 SQLite 单机实现，开启外键、WAL、busy timeout 和单写者事务。正式商业部署必须使用 PostgreSQL 适配器并通过单独迁移和恢复验证。
- Studio/Atelier 数据只通过只读 artifact resolver 暴露。`project_id`、`video_url` 和客户端提交的 URL 不是所有权证明。
- 平台支付在 Gate 4 之前只允许模拟不可变账本；不得调用真实支付渠道或开放真实提现。

## 影响

平台代码放在 `src/apps/platform/`，但首个实现切片不修改 Studio/Atelier 状态模型。官方应用装配点只有在 feature flag 与回归测试就绪后才允许增加显式 hook。

## 回滚

关闭 `LUMENX_PLATFORM_ENABLED` 即可停用平台路由。平台数据库和平台输出目录保持原样，不删除、不回写 Studio/Atelier 数据。迁移只允许向前兼容；有问题时回滚到上一应用提交并保留数据库快照，由前向修复脚本处理数据。

## 不在本决策中的事项

支付渠道、税务处理、创作者作品许可期限和生产 PostgreSQL 拓扑需要 Gate 4 前另行批准。本 ADR 不代表真实商业结算已实现。
