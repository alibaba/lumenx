# PR Summary

- 改动目标：
- 主要变更：
- 风险点 / 影响面：

## Verification

- [ ] 前端相关改动已执行 `npm run quality:frontend`
- [ ] 后端相关改动已执行 `pytest -q`
- [ ] 已执行与本次改动相关的最小验证
- [ ] 已说明未执行的验证项及原因（如有）
- [ ] 已确认分支命名与 commit message 符合仓库 Git 规范

## Copy / i18n Checklist

- [ ] 本次新增的用户可见文案，已判断是否属于英文保留白名单
- [ ] 不属于白名单的用户可见文案，已迁入 `frontend/src/lib/i18n/zh-CN.ts`，未在 `tsx/ts` 中硬编码
- [ ] 属于白名单的内容（品牌名、模型名、Prompt 语法、URL、环境变量、格式名等）保留英文原文
- [ ] 需要同时兼顾理解与识别的术语，已采用“中文主 + 英文辅”或“值英文、显示中文 label”的模式
- [ ] 若新增了新的英文保留项，已同步更新 `docs/中文化清单_影视创作者.md`
- [ ] 若新增内容会被巡检脚本误报，已同步评估是否需要更新 `frontend/scripts/audit-hardcoded-copy.mjs`

## Notes for Reviewers

- 建议重点关注：
- 需要一起确认的口径：
