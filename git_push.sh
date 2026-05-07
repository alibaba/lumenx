#!/bin/bash

# ========================================
# Git Workflow Helper Script
# 用法:
#   ./git_push.sh                    # 交互式提交到当前分支
#   ./git_push.sh "commit message"   # 快速提交到当前分支
#   ./git_push.sh -n feature-name    # 创建新功能分支并切换（默认基于 main）
#   ./git_push.sh -s branch-name     # 切换到已有分支（保留更改）
#   ./git_push.sh -l                 # 列出所有分支
# ========================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 获取当前分支
CURRENT_BRANCH=$(git branch --show-current)
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

# 显示帮助
show_help() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}Git Workflow Helper Script${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "用法:"
    echo "  ./git_push.sh                    # 交互式提交到当前分支"
    echo "  ./git_push.sh \"commit message\"   # 快速提交到当前分支"
    echo "  ./git_push.sh -n feature-name    # 创建新功能分支 (基于 main)"
    echo "  ./git_push.sh -s branch-name     # 切换到已有分支 (使用 stash 保留更改)"
    echo "  ./git_push.sh -l                 # 列出所有分支"
    echo "  ./git_push.sh -h                 # 显示帮助"
    echo ""
}

# 列出所有分支
list_branches() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}本地分支:${NC}"
    git branch
    echo ""
    echo -e "${GREEN}远程分支:${NC}"
    git branch -r
    echo -e "${BLUE}========================================${NC}"
}

# 创建新功能分支
create_feature_branch() {
    local FEATURE_NAME=$1
    local BASE_BRANCH="main"
    local NEW_BRANCH="feature/${FEATURE_NAME}"
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${YELLOW}Creating new feature branch: ${NEW_BRANCH}${NC}"
    echo -e "${YELLOW}Based on: ${BASE_BRANCH}${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # 检查是否有未提交的更改
    if [[ -n $(git status --porcelain) ]]; then
        echo -e "${YELLOW}Detected uncommitted changes. Stashing...${NC}"
        git stash push -m "Auto stash before creating branch ${NEW_BRANCH}"
        STASHED=true
    fi
    
    # 先更新 base branch
    echo "Fetching latest from origin..."
    git fetch origin
    
    # 检查 base branch 是否存在
    if git show-ref --verify --quiet refs/remotes/origin/${BASE_BRANCH}; then
        git checkout ${BASE_BRANCH}
        git pull origin ${BASE_BRANCH}
    else
        echo -e "${YELLOW}Base branch ${BASE_BRANCH} not found. Using main instead.${NC}"
        BASE_BRANCH="main"
        git checkout main
        git pull origin main
    fi
    
    # 创建新分支
    git checkout -b ${NEW_BRANCH}
    
    # 恢复 stash
    if [[ "$STASHED" == "true" ]]; then
        echo -e "${YELLOW}Restoring stashed changes...${NC}"
        git stash pop
    fi
    
    echo -e "${GREEN}✅ Successfully created and switched to ${NEW_BRANCH}${NC}"
}

# 切换分支（使用 stash 保留更改）
switch_branch() {
    local TARGET_BRANCH=$1
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${YELLOW}Switching to branch: ${TARGET_BRANCH}${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # 检查是否有未提交的更改
    if [[ -n $(git status --porcelain) ]]; then
        echo -e "${YELLOW}Detected uncommitted changes. Stashing...${NC}"
        git stash push -m "Auto stash before switching to ${TARGET_BRANCH}"
        STASHED=true
    fi
    
    # 切换分支
    git checkout ${TARGET_BRANCH}
    
    # 恢复 stash
    if [[ "$STASHED" == "true" ]]; then
        echo -e "${YELLOW}Restoring stashed changes...${NC}"
        git stash pop || {
            echo -e "${RED}⚠️ Stash pop failed (possibly conflicts). Use 'git stash list' and 'git stash show' to inspect.${NC}"
        }
    fi
    
    echo -e "${GREEN}✅ Successfully switched to ${TARGET_BRANCH}${NC}"
}

# 提交并推送
commit_and_push() {
    local COMMIT_MSG=$1
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}Git Push Process${NC}"
    echo -e "${YELLOW}Current branch: ${CURRENT_BRANCH}${NC}"
    echo -e "${BLUE}========================================${NC}"
    
    # 显示当前状态
    echo ""
    echo -e "${GREEN}Current status:${NC}"
    git status --short
    echo ""
    
    # 如果没有提交信息，交互式输入
    if [[ -z "$COMMIT_MSG" ]]; then
        echo -e "${YELLOW}Enter commit message (or press Enter for auto message):${NC}"
        read -r USER_MSG
        if [[ -n "$USER_MSG" ]]; then
            COMMIT_MSG="$USER_MSG"
        else
            COMMIT_MSG="chore: auto commit at $TIMESTAMP"
        fi
    fi
    
    echo ""
    echo -e "${GREEN}Commit message: ${COMMIT_MSG}${NC}"
    echo ""
    
    # 确认
    echo -e "${YELLOW}Proceed with commit and push? (y/n)${NC}"
    read -r CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo -e "${RED}Aborted.${NC}"
        exit 0
    fi
    
    # 添加所有更改
    echo "1. Adding all changes..."
    git add .
    
    # 提交更改
    echo "2. Committing changes..."
    git commit -m "$COMMIT_MSG"
    
    # 推送到远程仓库
    echo "3. Pushing to origin/${CURRENT_BRANCH}..."
    git push -u origin ${CURRENT_BRANCH}
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}✅ Successfully pushed to origin/${CURRENT_BRANCH}!${NC}"
    echo -e "${BLUE}========================================${NC}"
}

# 主逻辑
case "$1" in
    -h|--help)
        show_help
        ;;
    -l|--list)
        list_branches
        ;;
    -n|--new)
        if [[ -z "$2" ]]; then
            echo -e "${RED}Error: Please provide a feature name.${NC}"
            echo "Usage: ./git_push.sh -n feature-name"
            exit 1
        fi
        create_feature_branch "$2"
        ;;
    -s|--switch)
        if [[ -z "$2" ]]; then
            echo -e "${RED}Error: Please provide a branch name.${NC}"
            echo "Usage: ./git_push.sh -s branch-name"
            exit 1
        fi
        switch_branch "$2"
        ;;
    *)
        commit_and_push "$1"
        ;;
esac
