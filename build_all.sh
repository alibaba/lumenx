#!/bin/bash

# 全平台打包脚本(在对应平台上运行)

set -e

echo "======================================"
echo "云创 AI 漫剧 - 全平台打包脚本"
echo "======================================"

# 先构建前端项目
echo "1. 开始构建前端项目..."
if [ ! -d "frontend" ]; then
    echo "错误: frontend 目录不存在"
    exit 1
fi

cd frontend

# 检查 npm 或 yarn
if command -v yarn &> /dev/null; then
    echo "   使用 yarn 安装依赖..."
    yarn install
    echo "   使用 yarn 构建前端..."
    yarn build
elif command -v npm &> /dev/null; then
    echo "   使用 npm 安装依赖..."
    npm install
    echo "   使用 npm 构建前端..."
    npm run build
else
    echo "错误: 未找到 npm 或 yarn"
    exit 1
fi

cd ..
echo "   前端构建完成，输出目录: static/"
echo ""

# 检测操作系统并执行对应的打包脚本
echo "2. 开始打包应用程序..."

# 检测操作系统
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "检测到 macOS 系统，开始 Mac 打包..."
    bash build_mac.sh
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    echo "检测到 Windows 系统，开始 Windows 打包..."
    cmd //c build_windows.bat
else
    echo "不支持的操作系统: $OSTYPE"
    exit 1
fi

echo ""
echo "======================================"
echo "打包完成！"
echo "======================================"
