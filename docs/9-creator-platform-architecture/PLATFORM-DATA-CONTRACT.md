# 平台 Gate 0 数据与 API 合同

本文件把 `TECH-SPEC.md` 第 2.3、3.7–3.9、4.7 和第 7 节转换成可实现约束。它是平台第一切片的合同；没有对应测试的条款不能被视为已实现。

## 1. 配置与访问

| 配置 | 默认值 | 约束 |
|---|---|---|
| `LUMENX_PLATFORM_ENABLED` | `false` | false 时不装配平台路由和数据库 |
| `LUMENX_PLATFORM_DB_URL` | `sqlite:///output/platform.db` | SQLite 仅单机；生产使用 PostgreSQL |
| `LUMENX_PLATFORM_SESSION_TTL_SECONDS` | `86400` | 900–2,592,000 秒 |
| `LUMENX_PLATFORM_PASSWORD_MIN_LENGTH` | `12` | 不得低于 12 |

平台账户、角色和会话与部署级访问令牌分离。所有写请求必须带 `Idempotency-Key` 和服务端生成的 request id；重复 key 必须返回第一次的业务回执，参数冲突返回稳定错误码 `idempotency_key_reused`。

## 2. 数据分类与保留

| 类别 | 示例 | 存储 | 删除/保留 |
|---|---|---|---|
| 账户 PII | email、phone、昵称 | 平台库，加密/哈希按字段处理 | 用户请求删除后 30 天内匿名化，法定留存除外 |
| 认证材料 | 评审分数、评语、证书 | 平台库 | 认证历史保留 7 年，撤销访问后不再公开 |
| 成果快照 | artifact id/version/hash/media type | 平台库 | 跟随作品授权期限；URL 不作为事实 |
| 审计/账本 | 状态变化、借贷流水 | append-only 表 | 7 年；仅管理员可读 |
| 支付标识 | 渠道 token、脱敏末四位 | 加密字段/渠道 token | 关闭账户后 30 天内删除 |

平台不保存完整银行卡号、支付密码或第三方 JWT。原始媒体仍由 Core 的 managed media 存储负责，平台只保存不可变引用和解析时的快照。

## 3. 迁移、备份与恢复

- `schema_migrations(version, checksum, applied_at)` 是唯一迁移版本表；每个迁移文件只执行一次并校验 SHA-256。
- SQLite 连接必须启用 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`，写事务使用单写者锁。
- 每次迁移前复制数据库文件和 `schema_migrations`，备份命名包含版本与 UTC 时间；恢复后运行外键检查、账本借贷平衡检查和状态历史一致性检查。
- PostgreSQL 使用相同版本号和语义，不把 SQLite 文件复制到生产环境。
- 迁移失败只提交当前事务之前的内容；禁止通过删除数据库或静默重建空库恢复。

## 4. Artifact ownership resolver

提交请求可携带 `project_id`、`artifact_id` 和用户选择的 URL 作为解析输入。服务端必须向 Core 只读 resolver 请求：当前用户/环境归属、artifact version、媒体类型、内容哈希、项目标题快照和可访问路径。解析成功后把以下字段写入不可变 snapshot：

```text
artifact_id, artifact_version, sha256, media_type,
project_id, project_title_snapshot, resolved_at
```

解析失败、归属不符、版本不存在、媒体类型不允许或哈希变化都拒绝提交；平台不得从客户端 URL 推断权限或重新抓取任意远程地址。

## 5. 任务状态机

```text
draft -> published -> claimed -> in_progress -> delivered
                              -> revision_requested -> resubmitted -> accepted
                              -> settlement_pending -> settled -> closed
```

取消、逾期、释放、争议和退款是独立迁移。认领必须是 `UPDATE ... WHERE status='published' AND version=?` 的原子事务；成功后写状态历史、审计事件和原始认领回执。重复请求按幂等 key 返回原回执，不能再次绑定或扣费。自动释放由可恢复 worker 根据数据库中的 deadline 扫描执行，不能依赖进程内定时器。

## 6. API 合同模板

每个 endpoint 的实现文件必须同时声明：Pydantic 请求/响应模型、角色、字段限制、稳定错误码、幂等键、事务边界、审计字段、状态迁移和恢复动作。最小角色集合为 `user`、`creator`、`reviewer`、`client`、`admin`。

文件交付只接受受管理 artifact 引用；管理员/客户的验收权限与创作者提交权限分离。跨用户读取和所有状态写入必须服务端重新核验。

## 7. 模拟账本

Gate 4 之前仅实现 `ledger_accounts`、`ledger_entries`、`payout_requests`、`payout_attempts`、`reconciliation_batches` 和 `audit_events` 的模拟模式。`ledger_entries` append-only，金额用整数分，借贷总额必须平衡；钱包余额只能从账本重建。支付回调只接受带幂等键的模拟事件，未知状态进入人工复核队列，不自动重复支付。
