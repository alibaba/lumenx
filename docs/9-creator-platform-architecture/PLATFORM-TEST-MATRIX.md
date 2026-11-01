# 创作者平台测试映射

本表只记录平台新增边界，不改变 Core/Studio/Atelier 的 F01–F44 编号。

| 能力 | 当前入口 | 代表测试 | 当前状态 |
|---|---|---|---|
| 角色集合与任务状态迁移 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_platform_roles_and_task_transitions_are_explicit` | Gate 0 合同通过 |
| 邀请码注册与消费 | `registration_invites`; `/api/platform/auth/register`; `/api/platform/auth/invites` | `tests/test_platform_service.py::test_registration_requires_single_use_invite_when_enabled`; `tests/test_platform_skill_api.py::test_registration_api_accepts_and_consumes_invite` | 生产默认强制邀请码；一次性消费、过期/次数限制和管理员生成已通过 |
| artifact 不可变快照 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_artifact_snapshot_is_immutable_and_rejects_client_url_only_evidence` | Gate 0 合同通过 |
| 幂等上下文与整数分账本字段 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_idempotency_and_ledger_contracts_reject_invalid_values` | Gate 0 合同通过 |
| SQLite 迁移与恢复 | `src/apps/platform/db.py` | `tests/test_platform_db.py` | Sprint 1 第一切片通过 |
| 账户会话、撤销、角色授权 | `src/apps/platform/service.py` + `api.py` | `tests/test_platform_service.py::test_auth_session_revocation_and_password_policy` | Sprint 1 通过 |
| artifact 快照提交 | `PlatformService.submit_artifact` | `tests/test_platform_service.py` | 已通过；Core resolver 接线仍待替换 |
| 赛事白名单与报名幂等 | `PlatformService.enroll` | `tests/test_platform_service.py::test_claim_is_idempotent_and_competition_whitelist_is_enforced` | 已通过 |
| 认证考核与创作者晋级 | `PlatformService.apply_certification` / `review_certification` | `tests/test_platform_service.py::test_certified_creator_claims_delivers_and_review_creates_simulated_ledger` | 已通过 |
| 赛事详情、榜单与评审 | `PlatformService.competition` / `leaderboard` / `review_submission` | service coverage pending | 已实现 |
| 课程、模板 Fork | `PlatformService.add_course` / `fork_template` | API/service smoke coverage pending | 已实现 |
| 任务原子认领、交付、评审 | `PlatformService.claim_task` / `deliver_task` / `review_task` | `tests/test_platform_service.py::test_certified_creator_claims_delivers_and_review_creates_simulated_ledger` | 已通过 |
| 模拟账本与余额重建 | `PlatformService.wallet` | `tests/test_platform_service.py::test_certified_creator_claims_delivers_and_review_creates_simulated_ledger` | 已通过 |
| 截止时间释放与账单查询 | `PlatformService.release_expired_tasks` / `transactions` | service coverage pending | 已实现 |
| 系统 Skill 默认加载与目录快照 | `src/apps/atelier/product_skills.py` | `tests/test_atelier_product_skills.py` | 当前仅有静态系统 Skill；用户覆盖待实现 |
| Skill 版本哈希与父版本关系 | `skill_versions` / `effective_skill_snapshot` | 待新增 Skill contract tests | 方案已定义，未实现 |
| 用户/项目 Skill 绑定与回滚 | `skill_bindings` | 待新增 API/service tests | 方案已定义，未实现 |
| Skill 工具与成本权限交集 | Agent admission/authorization | 待新增 authorization tests | 不得通过 Skill 绕过审批或计费 |
| Skill 运行快照与任务恢复 | `skill_runs` + generation receipt | 待新增 recovery tests | 已批准任务必须固定原 Skill 快照 |
| 用户反馈驱动的 Skill 草稿 | `skill_feedback` + sandbox evaluator | 待定义 | 不自动覆盖线上版本 |
| Skill 版本与绑定持久化 | `platform_skill_versions` / `platform_skill_bindings` | `tests/test_platform_db.py`；`tests/test_platform_service.py` | 当前切片通过；HTTP/API 与 UI 待接线 |
| 用户/项目/系统解析顺序 | `PlatformService.resolve_skill_version` | `tests/test_platform_service.py::test_skill_versions_are_user_isolated_and_resolve_project_then_user` | 当前切片通过 |
| Skill manifest 安全字段与哈希 | `PlatformService.create_skill_version` | `tests/test_platform_service.py::test_skill_manifest_rejects_executable_fields_and_bad_hash` | 当前切片通过；尚未接 Product Skill snapshot converter |
| Skill API 创建/列表/绑定/解析 | `src/apps/platform/api.py` | `tests/test_platform_skill_api.py` | 当前切片通过 |
| Skill 前端账户/项目绑定界面 | `frontend/src/components/platform/SkillSettings.tsx`；`frontend/src/lib/platformApi.ts` | `npm --prefix frontend run typecheck`; `npm --prefix frontend run build` | 当前切片通过；组件交互回归仍可扩展 |
| Platform manifest → Atelier snapshot converter | `src/apps/atelier/skill_runtime.py` | `tests/test_atelier_skill_runtime.py` | 当前切片通过 |
| Skill static sandbox rejection | `src/apps/atelier/skill_runtime.py` | `tests/test_atelier_skill_runtime.py` | 当前切片通过；仅静态校验，不执行脚本 |
| Video admission Skill snapshot binding | `src/apps/atelier/generation_admission.py`；`generation_ledger.py` | `tests/test_atelier_generation_admission.py` | 当前切片通过；绑定写入 candidate projection 和 request fingerprint |
| Generation attempt Skill binding persistence | `src/apps/atelier/models.py`；Atelier storage readback | `tests/test_atelier_generation_admission.py`；`tests/test_atelier_agent_loop_storage.py` | 当前切片通过 |
| Platform-to-Atelier runtime resolver | `src/apps/atelier/skill_runtime.py`；`agent_loop.py` | `tests/test_atelier_skill_runtime.py` | 当前切片通过；由组合根注入平台 manifest |
| 真实请求上下文 Skill bridge | `src/apps/atelier/agent_loop_api.py`；platform middleware actor | `tests/test_atelier_skill_runtime.py` | 当前切片通过；用户/项目绑定按认证 actor 解析 |
| 隔离 Skill worker job contract | `src/apps/atelier/skill_worker_contract.py` | `tests/test_atelier_skill_worker_contract.py` | 当前为合同/静态边界，worker 集群未实现 |
| Skill run snapshot ledger | `platform_skill_runs` / `PlatformService` | `tests/test_platform_db.py`; `tests/test_platform_service.py` | 当前切片通过；持久化运行账本已建立 |
| Skill run API | `/api/platform/skills/runs` | 待新增 HTTP test | 当前已接线；Worker claim 尚待实现 |
| Skill run atomic claim / lease recovery | `PlatformService.claim_skill_run` / `recover_expired_skill_runs` | `tests/test_platform_service.py` | 当前切片通过；同一活跃租约不可重复 claim |
| Worker identity、heartbeat、跨进程 claim | `platform_skill_workers`；`PlatformService.claim_skill_run_with_token` | `tests/test_platform_service.py` | 当前切片通过；仍是持久化 SQLite 队列合同 |
| Redis/PostgreSQL queue config and worker isolation | `src/apps/platform/queue_backend.py`; `Dockerfile.skill-worker`; Compose profile | `tests/test_skill_queue_backend.py`; `docker compose config`; live Redis smoke | 配置合同、隔离配方和 Redis 容器启动/健康通过；PostgreSQL 容器健康通过，adapter live claim 待补 |
| Central queue adapters and isolated runner | `src/apps/platform/central_queue.py`; `src/apps/platform/skill_worker_main.py`; `scripts/verify_live_skill_queue.py` | `tests/test_central_skill_queue.py`; `tests/test_skill_worker_main.py`; Redis XADD/XPENDING smoke; PostgreSQL 24-job/6-worker smoke | Fake runner、Redis live consume/ack、PostgreSQL 真实 enqueue/claim/ack 与并发去重通过；Provider handler 仍为 dry-run |
| Worker monitoring and credential proxy boundary | `src/apps/platform/worker_runtime.py`; `src/apps/platform/credential_proxy_service.py`; Compose monitoring profile | `tests/test_worker_runtime.py`; `tests/test_credential_proxy_service.py`; Worker `/metrics`; Prometheus target; OTel Collector logs | 结构化无 payload 指标、真实代理 issue/resolve、Prometheus scrape 和 OTel scrape 通过；生产 Secret Manager、轮换、告警路由待接入 |
| Multi-user queue pressure and production profile | `scripts/verify_live_skill_queue.py`; `docker-compose.yml` | PostgreSQL 240-job/12-worker/24-tenant smoke; `docker compose --profile skill-production --profile monitoring up` | 240/240 claim，0 duplicate，每租户 10 个；本机生产 profile 通过，云端 HA/弹性/故障演练待补 |
| Production readiness gate and alerting | `scripts/verify_skill_production.py`; `deploy/prometheus/alerts.yml`; `deploy/otel/otel-collector-remote.yml` | proxy/metrics/Prometheus/OTel readiness smoke | 本机门禁和告警/远端 OTLP 配置模板通过；云端 Secret Manager、远端 exporter 和故障演练需部署参数 |
| Mounted secret provider and rotation | `FileSecretProvider`; `deploy/credential-proxy-production.compose.yml` | `tests/test_credential_proxy_service.py::test_file_secret_provider_reads_rotated_secret`; production gate provider=file | 文件 provider 轮换读取通过；真实云 Secret Manager/KMS CSI 挂载仍需云端参数 |
| Redis pending recovery and cloud profile | `RedisStreamQueue.claim`; `deploy/skill-production.compose.yml` | `tests/test_central_skill_queue.py::test_redis_queue_reclaims_pending_message_before_new_messages`; `scripts/verify_redis_worker_recovery.py` | XAUTOCLAIM 恢复合同和本机现场演练通过；云编排器副本、滚动升级和跨 AZ 故障演练待执行 |
| Kubernetes/ECS runtime safety template | `deploy/k8s/skill-runtime.yaml`; `scripts/validate_skill_k8s.py` | static manifest safety gate; production image gate | 双副本、PDB、非 root、只读根、drop ALL、Secret mount 和无 hostPort 通过；真实 image prefix/tag 需显式替换，实际云编排器部署待执行 |
| Real image rollout and fault drill workflow | `scripts/deploy_skill_runtime.py`; `scripts/verify_k8s_fault_drill.py` | client dry-run; non-mutating drill test | 发布前置条件、server-side rollout 和显式故障演练命令已固化；当前环境无 registry/Kubernetes context，未执行真实 apply |

验证命令：

```bash
.venv/bin/python -S scripts/run_tests_isolated.py -- tests/test_platform_contracts.py tests/test_platform_db.py tests/test_platform_service.py -q
python3 docs/agents/tools/check_lumenx_refactor_docs.py
```

测试通过只表示当前切片的合同约束通过，不表示平台 HTTP、真实支付或生产部署已验收。
