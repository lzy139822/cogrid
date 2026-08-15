#!/bin/bash
# Cogrid GitHub 推送脚本
# 用法：
#   1. 解压 cogrid.zip
#   2. 在 GitHub 上创建空仓库（不要勾选 README/LICENSE/gitignore）
#   3. 修改下面的 REPO_URL 为你的仓库地址
#   4. 运行: bash push-to-github.sh

set -e

# ===== 修改这里 =====
REPO_URL="https://github.com/YOUR_USERNAME/cogrid.git"
# ====================

echo ">>> Cogrid GitHub 推送脚本"
echo ">>> 目标仓库: $REPO_URL"
echo ""

# 检查是否在 cogrid 目录
if [ ! -d ".git" ]; then
    echo "错误：请在解压后的 cogrid 目录中运行此脚本"
    exit 1
fi

# 检查 git
if ! command -v git &> /dev/null; then
    echo "错误：未安装 git，请先安装"
    exit 1
fi

# 验证已有提交
echo ">>> 当前提交历史："
git log --oneline
echo ""

# 添加远程仓库
echo ">>> 添加远程仓库..."
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

# 推送
echo ">>> 推送到 GitHub..."
git push -u origin main

echo ""
echo ">>> 推送成功！"
echo ">>> 你的项目已在: ${REPO_URL%.git}"
echo ""
echo ">>> 下一步："
echo "    1. 邀请协作者：GitHub 仓库 → Settings → Collaborators"
echo "    2. 创建 Issues 标记点火 MVP 任务（参考 PROJECT_STATUS.md）"
echo "    3. 开启 GitHub Projects 看板管理任务"
