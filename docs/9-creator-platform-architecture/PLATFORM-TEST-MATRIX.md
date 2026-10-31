# 创作者平台测试映射

本表只记录平台新增边界，不改变 Core/Studio/Atelier 的 F01–F44 编号。

| 能力 | 当前入口 | 代表测试 | 当前状态 |
|---|---|---|---|
| 角色集合与任务状态迁移 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_platform_roles_and_task_transitions_are_explicit` | Gate 0 合同通过 |
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

验证命令：

```bash
.venv/bin/python -S scripts/run_tests_isolated.py -- tests/test_platform_contracts.py tests/test_platform_db.py tests/test_platform_service.py -q
python3 docs/agents/tools/check_lumenx_refactor_docs.py
```

测试通过只表示当前切片的合同约束通过，不表示平台 HTTP、真实支付或生产部署已验收。
