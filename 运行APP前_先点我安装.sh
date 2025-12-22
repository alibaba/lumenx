#!/bin/bash

# 云创AI漫剧 安装脚本
# Installation script for 云创AI漫剧

APP_NAME="云创AI漫剧.app"
INSTALL_DIR="/Applications"
INSTALL_PATH="$INSTALL_DIR/$APP_NAME"

echo "================================"
echo "云创AI漫剧 安装程序"
echo "================================"
echo ""

# 检查是否以管理员权限运行
if [ "$EUID" -ne 0 ]; then 
    echo "此脚本需要管理员权限来安装应用程序。"
    echo "正在请求管理员权限..."
    echo ""
    
    # 重新以sudo权限运行自己
    osascript -e "do shell script \"$0\" with administrator privileges"
    exit $?
fi

# 获取脚本所在目录（DMG挂载点）
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SOURCE_APP="$SCRIPT_DIR/$APP_NAME"

echo "步骤 1/3: 将应用程序复制到 Applications 目录..."

# 检查源应用是否存在
if [ ! -d "$SOURCE_APP" ]; then
    echo "错误: 找不到 $APP_NAME"
    echo "请确保应用程序在DMG中与此脚本位于同一目录。"
    exit 1
fi

# 如果应用已存在，先删除
if [ -d "$INSTALL_PATH" ]; then
    echo "检测到已安装的旧版本，正在删除..."
    rm -rf "$INSTALL_PATH"
fi

# 复制应用到Applications目录
cp -R "$SOURCE_APP" "$INSTALL_DIR/"

if [ $? -ne 0 ]; then
    echo "错误: 复制应用程序失败"
    exit 1
fi

echo "✓ 应用程序已成功复制到 $INSTALL_PATH"
echo ""

echo "步骤 2/3: 移除系统隔离属性..."

# 移除quarantine属性
xattr -r -d com.apple.quarantine "$INSTALL_PATH" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "✓ 已成功移除隔离属性"
else
    echo "⚠ 移除隔离属性时出现警告（可能属性不存在，这是正常的）"
fi

echo ""

echo "步骤 3/3: 启动应用程序..."
echo ""

# 打开应用
open "$INSTALL_PATH"

echo "================================"
echo "安装完成！"
echo "$APP_NAME 已成功安装并启动。"
echo "================================"
echo ""
echo "您可以关闭此窗口并弹出DMG镜像。"
echo ""

exit 0
