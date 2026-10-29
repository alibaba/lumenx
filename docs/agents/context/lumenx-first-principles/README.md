# LumenX 产品与验证规范

公开材料整理：2026-10-30。本目录来自研发快照 `7765f9e816088a88f8489a737ad70eb7211de051`。产品决定保持原义；公开候选的验证状态与来源版本历史结果分别记录。

## 从这里开始

1. 判断产品行为：读 [产品契约](PRODUCT-CONTRACT.md) 与对应 ADR。
2. 修改实现：读 [架构责任](ARCHITECTURE-DRAFT.md) 和 [计划的依赖与门禁](REFACTOR-PLAN.md#execution-order)，再核对 [公开候选台账](EXECUTION-STATUS.md)。
3. 写回归与验收：从 [24 组产品旅程](PRODUCT-JOURNEYS.md) 映射到 [44 个功能组及实际测试入口](TEST-MATRIX.md)。
4. 判断完成：只使用与候选源码绑定的实际运行结果。源码、测试文件、计划批准和旧版本通过均不能替代当前验收。
5. 安装与迁移：读 [本地及远程服务配方](../../../operations/headless.md) 和 [迁移说明](../../deliverables/github-release-2026-10-30/MIGRATION.md)。

## 材料职责

| 文档 | 职责 |
|---|---|
| [README.md](README.md) | 公开规范入口与权威关系 |
| [PRODUCT-CONTRACT.md](PRODUCT-CONTRACT.md) | 产品哲学、Studio/Atelier/Core 边界和本轮范围 |
| [CONTEXT.md](CONTEXT.md) | 创作领域词汇 |
| [ARCHITECTURE-DRAFT.md](ARCHITECTURE-DRAFT.md) | 目标责任与依赖；保留历史文件名 |
| [REFACTOR-PLAN.md](REFACTOR-PLAN.md) | P0–P10 切片、依赖、行为门禁和回滚规则 |
| [PRODUCT-JOURNEYS.md](PRODUCT-JOURNEYS.md) | SJ/AJ/CJ 行为验收规格与历史发现的反例 |
| [TEST-MATRIX.md](TEST-MATRIX.md) | F01–F44 到当前生产入口和代表性测试的可执行导航 |
| [EXECUTION-STATUS.md](EXECUTION-STATUS.md) | 来源证据边界、公开候选验证状态与剩余现场工作 |
| [HISTORICAL-FINDINGS.md](HISTORICAL-FINDINGS.md) | 未公开旧审查的发现编号和处理约束摘要；不是原报告或通过证明 |
| [F22-PROVIDER-CONTRACT-NOTES.md](F22-PROVIDER-CONTRACT-NOTES.md) | 2026-10-13 供应商公开资料核对记录；使用前仍须复核实际 API 版本 |
| [ADR-0001](adr/0001-local-then-remote-before-collaboration.md) | 单人本地、远程与后续多人阶段 |
| [ADR-0002](adr/0002-local-default-authoritative-project-service.md) | 本地默认、正式状态、双入口与独立复制 |
| [ADR-0003](adr/0003-shared-core-contracts-with-explicit-entry-capabilities.md) | 共同合同与入口能力子集 |
| [ADR-0004](adr/0004-core-resolves-generation-defaults.md) | 默认值、冻结参数/媒体及重试 |
| [ADR-0005](adr/0005-recover-submitted-work-without-resubmission.md) | 原任务查询恢复与重新付费的区别 |
| [ADR-0006](adr/0006-converge-execution-with-product-owned-state.md) | 共用执行协议、产品状态、单写者与正式桌面路径 |

## 权威与证据边界

用户最新明确要求优先；已采纳的产品契约和 ADR 约束实施计划，计划约束代码。实际证据决定目前做到了什么，不能把缺陷或测试现状反写为产品需求。普通修复只更新受影响的规范、测试映射与结果，不创建另一份竞争进度表。

原始 `SESSION.md`、完整研发台账、审查包、浏览器录像/截图、日志与机器清单保留在上述来源快照及研发分支中，未随本公开候选发布。它们不是公开仓库中的可点击证据，也不应被批量拷贝回来。公开文档引用的历史计数只能说明来源记录了什么；发布分支的测试应重新运行并绑定实际提交或源码清单。新的可公开结果统一由 [发布准备记录](../../deliverables/github-release-2026-10-30/README.md) 导航。

P、F、SJ/AJ/CJ 与历史发现 ID 保持稳定，不把旧编号复用于新功能。历史缺口已经经过后续修改，保留其反例不表示当前仍然存在；具体状态由候选检查决定。任何经过删减的公开摘要都不冒充完整审计备份。

## 文档检查

```bash
python3 docs/agents/tools/check_lumenx_refactor_docs.py
python3 -B docs/agents/tools/test_check_lumenx_refactor_docs.py -v
```

检查保留文件及本仓链接、ADR 状态、阶段与功能/场景/发现编号映射；不导入应用、不执行产品测试、不判断审美或供应商质量。缺文件、失效链接和非法映射必须失败，不能通过忽略全部历史目录把检查变成无条件成功。

[检查器回归](../../tools/test_check_lumenx_refactor_docs.py)包含正常文档和受控坏例；只在内存替换读取内容，不修改项目或个人数据。导航页不能充当“已验证”阶段所需的实际运行记录。

