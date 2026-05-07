# 版本管理与 Git 规范

这份文档是本仓库的 Git 使用约定。目标很简单：分支清晰、提交清晰、版本清晰。

## 1. 分支策略

- `main`：稳定主线，只接收已经评审完成的合并结果。
- `release/*`：发布准备、回滚修复、版本号整理。
- `feature/*`：新功能。
- `fix/*`：缺陷修复。
- `docs/*`：文档改动。
- `refactor/*`：不改变行为的重构。
- `test/*`：测试补充或修正。
- `chore/*`：工具、脚本、依赖、维护类改动。
- `codex/*`：AI 辅助工作分支，适合短期试验、拆分和整理。

建议原则：

- 一个分支只做一类事情。
- `main` 不作为日常工作分支。
- 需要长期保留的工作，应尽快从 `codex/*` 收敛到正式分支前缀。

## 2. 提交规则

本仓库使用 Conventional Commits。

格式：

```text
type(scope): subject
```

允许的常见 type：

- `feat`
- `fix`
- `docs`
- `style`
- `refactor`
- `test`
- `chore`
- `build`
- `ci`
- `perf`
- `revert`

建议：

- 一次提交只表达一个意图。
- 逻辑改动、测试改动、文档改动尽量拆开。
- 不要把大规模格式化和业务变更混在同一个提交里。
- `subject` 保持简短、可读、可回溯。

示例：

```text
feat(storyboard): add frame grouping controls
fix(video): handle missing temp url fallback
docs(git): document release and branch policy
```

## 3. 版本规则

- 发布版本以 Git tag 为准，建议使用 `vMAJOR.MINOR.PATCH`。
- 正式发布前，先在 `release/*` 分支完成收口。
- 发布说明建议使用 `chore(release): vX.Y.Z` 这一类提交。
- `package.json` 里的版本号只作为前端包元数据时再同步，不要在功能分支里随手改版本号。

## 4. 本地执行

首次克隆后建议执行：

```bash
npm run git:setup-hooks
```

这会把 `.githooks/` 设为当前仓库的本地 hooks 路径，并启用以下检查：

- `commit-msg`：检查当前分支与提交信息格式。
- `pre-push`：阻止把普通工作分支推到不合规的命名，默认也阻止直接推送 `main`。

如确需对 `main` 做特殊维护操作，可以显式设置，然后再执行对应的 commit 或 push：

```bash
ALLOW_MAIN_PUSH=1 git commit -m "chore(release): vX.Y.Z"
ALLOW_MAIN_PUSH=1 git push
```

## 5. 推荐工作流

1. 先同步 `main`。
2. 再创建新分支。
3. 小步提交，保持 commit 语义单一。
4. PR 里说明变更目标、验证方式和风险。
5. 合并后再考虑打 tag。

## 6. 最低要求

- 分支命名可读。
- commit message 可读。
- 每个 PR 有清晰边界。
- 版本号只在发布节点收口。
