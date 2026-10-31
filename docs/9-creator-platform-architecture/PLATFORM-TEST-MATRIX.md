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

验证命令：

```bash
.venv/bin/python -S scripts/run_tests_isolated.py -- tests/test_platform_contracts.py tests/test_platform_db.py tests/test_platform_service.py -q
python3 docs/agents/tools/check_lumenx_refactor_docs.py
```

测试通过只表示当前切片的合同约束通过，不表示平台 HTTP、真实支付或生产部署已验收。
