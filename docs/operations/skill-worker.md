# Skill Worker 隔离方案

当前实现的是任务合同，不是已经上线的分布式 worker。`src/apps/atelier/skill_worker_contract.py` 定义了未来队列消息的最小边界：用户、项目、Skill 快照绑定、请求摘要、允许工具和状态。应用组合根的真实请求上下文已经能把认证平台 Skill manifest 传给 Atelier loop；它仍在 API/Atelier 进程内完成解析，不等于已启用 worker 集群。

Worker 进入生产前必须满足：

- 一个任务只由一个 worker claim，使用持久化 job id 和幂等键；
- worker 使用独立进程或容器，不继承 API 进程的环境变量、凭证和用户数据根；
- 默认无网络、无脚本、无任意路径访问，只能读取受管理 artifact；
- Provider 凭证不进入 Skill job；真实生成由 Core 的已批准执行服务完成；
- 输出写入 staging，完成后用内容哈希和任务身份提交回 API；
- worker 重启只能恢复原 job，不得根据 Skill 或请求重新提交付费任务；
- 任务状态、Skill 快照哈希、输入/输出摘要和失败原因写入审计记录。

单机 MVP 可以先在同机以受限子进程运行 dry-run evaluator；多用户生产再迁移到独立队列、PostgreSQL job ledger、对象存储和隔离容器。当前仓库没有把该合同误报为已完成的 worker 集群。

平台 `platform_skill_runs` 是当前运行账本：创建按用户和 request fingerprint 幂等，状态更新只能由所属用户或后续受控 Worker 入口执行。当前已实现 `queued → running` 的原子 claim、Worker lease owner、过期回收、attempt_count、Worker identity token 和 heartbeat；两个 `PlatformService` 实例可以共享 SQLite 账本完成跨进程语义测试。

队列后端由 `LUMENX_SKILL_QUEUE_BACKEND` 显式选择：`sqlite` 是本地回退，`redis` 需要 `LUMENX_SKILL_REDIS_URL`，`postgres` 需要 `LUMENX_SKILL_POSTGRES_URL`。`src/apps/platform/central_queue.py` 提供 Redis Streams、PostgreSQL `SKIP LOCKED` 和统一 Worker runner；`Dockerfile.skill-worker` 和 Compose 的 `skill-production` profile 提供只读根文件系统、丢弃 Linux capabilities、无新权限和 noexec 临时目录的容器边界。2026-10-31 使用独立 Compose project `lumenx-live` 完成 Redis → Worker 容器真实 smoke：写入 `live-redis-2` 后被消费并 ack，`XPENDING lumenx:skill-runs lumenx-workers` 为 `0`。PostgreSQL 16 容器已通过 `pg_isready` 健康检查，但 adapter 的真实 enqueue/claim/ack 尚未执行。真实 Provider 调用仍需显式注入受控 handler；当前入口是 dry-run。

2026-11-01 已通过 loopback `127.0.0.1:15432` 执行 PostgreSQL 真实 smoke：24 个 job 由 6 个并发 Worker claim，`claimed=24`、`unique=24`、`duplicates=0`，随后全部 ack。可复用命令为 `LUMENX_SKILL_POSTGRES_URL=postgresql://... python scripts/verify_live_skill_queue.py --jobs 24 --workers 6`。该脚本只做队列层验收，不调用 Provider。

Worker 监控当前以结构化日志提供 `worker_id`、claimed/succeeded/failed、最近 job 和错误类型，绝不输出 job payload。Provider job 必须通过 `LUMENX_CREDENTIAL_PROXY_URL` 访问凭证代理；队列只携带 provider、scope 等非 secret 字段，代理返回 opaque `credential_ref`。未配置代理时 provider job fail-closed；`LUMENX_CREDENTIAL_PROXY_TOKEN` 只用于 Worker 到代理的认证。

当前生产配方新增 `credential-proxy`、Prometheus 和 OTel Collector：凭证代理监听 loopback `17178`，只允许 Worker issue 和 Core resolve 两种带 bearer token 的路径，引用默认 300 秒过期；Worker `/metrics` 监听 `9100`，Prometheus/OTel 通过容器网络抓取。示例配置使用环境变量注入，生产环境应替换为云 Secret Manager/KMS，不把真实 key 写进 Compose 或 job。

2026-11-01 现场验收：凭证代理 `/healthz`、issue/resolve 通过；Prometheus target `lumenx-skill-worker` 为 `up`；OTel Collector 日志确认收到 9 个 Worker 指标数据点。多用户压力脚本运行 240 个任务、12 个并发 Worker、24 个租户，`duplicates=0`、`unique=240`，每租户 10 个任务。
