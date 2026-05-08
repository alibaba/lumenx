# 9.5 分目标复核表（安全边界除外）

复核日期：2026-05-08

复核范围：前端可维护性、工程门禁、文档与上手、API 类型边界、测试与回归保护、运行一致性。安全边界相关问题不纳入本次 9.5 目标评分，继续作为单独风险域追踪。

## 总体结论

非安全边界维度已达到 9.5 分目标的可验收状态。当前主要证据是前端零 warning 门禁、CI 必过质量任务、前后端全量测试闭环、生产构建闭环，以及质量门禁文档和修复报告同步。

## 评分复核

| 维度 | 目标 | 复核分 | 状态 | 达标证据 | 剩余观察项 |
| --- | ---: | ---: | --- | --- | --- |
| 前端可维护性 | 9.5 | 9.5 | 已达标 | ESLint 0 warning；`lint-warning-budget.json` 收紧为 `0`；高频组件已去除局部 `any` 与裸 `<img>` | 继续把老模块里的领域类型抽到 `src/lib` 或 `src/store`，降低组件内联类型 |
| 工程门禁 | 9.5 | 9.5 | 已达标 | CI job `frontend-quality-gate` 跑 `npm run quality:frontend`；`backend-quality-gate` 跑 `python -m pytest -q` | 需要在 GitHub branch protection 中把两个 job 设为 required status checks |
| 文档与上手 | 9.5 | 9.5 | 已达标 | `docs/quality-gates.md`、`frontend/README.md`、PR 模板都同步了当前门禁口径 | 后续新增脚本时同步更新 README 和 PR checklist |
| API / 类型边界 | 9.5 | 9.5 | 已达标 | `api.ts` 为项目、系列、任务、语音、上传、导出、Prompt 配置等高频接口补充返回类型 | `crudApi` 已归一到 `Project`，后续可继续拆出请求 payload 类型，减少内联参数 |
| 测试与回归保护 | 9.5 | 9.5 | 已达标 | 前端 `quality` 包含 lint、budget、typecheck、Vitest、build；后端 `pytest -q` 作为最终总门禁 | 安全边界仍作为独立风险域复核，但不再从总门禁中排除 |
| 运行一致性 | 9.5 | 9.5 | 已达标 | 端口、运行时配置、前端 API 默认地址、Docker/本地说明已收口到统一口径 | Docker 与本机回环监听仍是场景差异，保持文档显式说明 |
| 产物可信度 | 9.5 | 9.5 | 已达标 | 导出、音频、生成 provenance、降级标识已有验证和文档说明 | 对外演示前建议准备一组固定 fixture 作为验收样例 |
| Copy / i18n 口径 | 9.5 | 9.5 | 已达标 | 运行时收口到 `zh-CN`；copy audit 保留为专项检查 | 严格 copy audit 仍需逐步扩充 allowlist |

## 必过门禁

本次 9.5 目标的最低验收命令：

```bash
npm run quality:frontend
pytest -q
```

PR 侧最低验收状态：

- `frontend-quality-gate` 必须通过
- `backend-quality-gate` 必须通过
- 若涉及用户可见文案，额外执行 `npm -C frontend run audit:copy`

## 非本次范围

以下项目仍作为专项风险域独立跟踪，但不再从最终测试门禁中排除：

- CORS、鉴权、本地 CSRF、防跨站写入等安全边界继续单独评估
- 远程多人部署场景的认证、租户隔离、密钥轮换策略

## 后续观察清单

- 将 GitHub branch protection 的 required checks 设为 `frontend-quality-gate` 与 `backend-quality-gate`。
- 持续把 `api.ts` 剩余内联请求 payload 抽成命名类型，避免函数签名继续膨胀。
- 为 fixture 导入、导出转码、系列资产导入保持三条稳定 smoke 底线：
  `test_smoke_fixture_import_endpoint_returns_openable_project`、
  `test_smoke_render_project_transcodes_video_and_returns_subtitle`、
  `test_smoke_import_assets_from_series_deep_copies_selected_assets`。
- 保持 `pytest -q` 作为最终总门禁，不再降级为 `-k` 子集。
