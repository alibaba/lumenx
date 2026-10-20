#!/bin/bash

# ========================================
# 安装 git-safe-push 本地钩子
# 将 scripts/git-hooks/{pre-commit,pre-push} 软链到 .git/hooks/
# 幂等，可重复执行；不触碰其他钩子（如 post-commit）
# ========================================

set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOKS_SRC="$REPO_ROOT/scripts/git-hooks"
HOOKS_DIR=$(git rev-parse --git-path hooks)

for hook in pre-commit pre-push; do
    src="$HOOKS_SRC/$hook"
    dst="$HOOKS_DIR/$hook"
    chmod +x "$src"
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        echo "⚠️  $dst 已存在且不是软链，备份为 $hook.bak"
        mv "$dst" "$dst.bak"
    fi
    ln -sf "$src" "$dst"
    echo "✅ 已安装 $hook -> $dst"
done

echo "完成。跳过检查（仅紧急情况）: SKIP_SAFE_PUSH=1 git commit/push ..."
