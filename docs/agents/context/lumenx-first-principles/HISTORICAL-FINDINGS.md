# 历史发现编号与规范映射

这是一份公开摘要，不是原审查报告或当前缺陷清单。来源是研发快照 `7765f9e816088a88f8489a737ad70eb7211de051` 中的 2026-10-07 架构审查、2026-10-08 Core 可行性报告和完整台账；原源码定位、截图及执行证据未随公开候选发布。

下列编号保留旧报告的议题与实施归属。后续实现已经发生变化，不能依据历史标题认定缺陷仍存在，也不能依据摘要认定已修复。候选验收见 [台账](EXECUTION-STATUS.md)，行为约束见 [计划](REFACTOR-PLAN.md) 与 [测试矩阵](TEST-MATRIX.md)。旅程的 S-R1–S-R5/A-R1–A-R4 定义在 [产品旅程](PRODUCT-JOURNEYS.md)。

| 历史发现 | 规范负责切片 | 验收索引 |
|---|---|---|
| ARCH:R1 Studio 保存 | P2M | F01/F06/F44；CJ-05 |
| ARCH:R2 网络访问边界 | P7 | F19/F21/F43；CJ-06/08 |
| ARCH:R3 Tauri 生命周期 | P7/P10 | F23；CJ-06 |
| ARCH:R4 节点字段覆盖 | P2A | F25/F27/F28；CJ-05 |
| ARCH:R5 单轮 SSE 派发 | P4 | F35/F38；AJ-03/CJ-02 |
| ARCH:R6 入口能力差异 | P3/P4 | F11–14/F30/F36/F37；CJ-01 |
| ARCH:R7 Wan 音频语义 | P3/P5 | F12/F30；CJ-01 |
| ARCH:R8 workspace 配置 | P3/P7 | F21/F43；CJ-01/06 |
| ARCH:R9 资产任务恢复 | P6 | F04/F15/F35；CJ-04 |
| ARCH:R10 单写者 | P2M/P2A | F44；CJ-05 |
| ARCH:R11 测试门禁 | P1/P9/P10 | F01–F44 |
| CORE:C1 请求外形 | P3 | F11/F30；CJ-01 |
| CORE:C2 Agent 字段落后 | P3/P4 | F30/F36/F37；AJ-03/CJ-01 |
| CORE:C3 接纳结果派发 | P4 | F35/F38；AJ-03/CJ-02 |
| CORE:C4 默认/校验分叉 | P3/P5 | F11–14/F30/F37；CJ-01/03 |
| CORE:C5 分支同名契约漂移 | P0/P5/P9 | F11/F12/F20/F22 |
