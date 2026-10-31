# 创作者平台测试映射

本表只记录平台新增边界，不改变 Core/Studio/Atelier 的 F01–F44 编号。

| 能力 | 当前入口 | 代表测试 | 当前状态 |
|---|---|---|---|
| 角色集合与任务状态迁移 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_platform_roles_and_task_transitions_are_explicit` | Gate 0 合同通过 |
| artifact 不可变快照 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_artifact_snapshot_is_immutable_and_rejects_client_url_only_evidence` | Gate 0 合同通过 |
| 幂等上下文与整数分账本字段 | `src/apps/platform/contracts.py` | `tests/test_platform_contracts.py::test_idempotency_and_ledger_contracts_reject_invalid_values` | Gate 0 合同通过 |
| SQLite 迁移与恢复 | `src/apps/platform/db.py` | `tests/test_platform_db.py` | Sprint 1 第一切片通过 |
| 账户会话、撤销、越权 | 待建立平台 router | 待建立 | Sprint 1 |
| artifact ownership resolver | 待接 Core 只读 resolver | 待建立 | Sprint 1 |
| 赛事、评审、模板 Fork | 待建立 | 待建立 | Sprint 2 |
| 任务原子认领与模拟账本 | 待建立 | 待建立 | Sprint 3 |

验证命令：

```bash
.venv/bin/python -S scripts/run_tests_isolated.py -- tests/test_platform_contracts.py -q
python3 docs/agents/tools/check_lumenx_refactor_docs.py
```

测试通过只表示当前切片的合同约束通过，不表示平台 HTTP、真实支付或生产部署已验收。
