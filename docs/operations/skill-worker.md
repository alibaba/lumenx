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

平台 `platform_skill_runs` 是当前运行账本：创建按用户和 request fingerprint 幂等，状态更新只能由所属用户或后续受控 Worker 入口执行。当前已实现 `queued → running` 的原子 claim、Worker lease owner、过期回收、attempt_count、Worker identity token 和 heartbeat；两个 `PlatformService` 实例可以共享 SQLite 账本完成跨进程语义测试。真正的 Redis/PostgreSQL 队列和独立 Worker 集群仍待实现。
