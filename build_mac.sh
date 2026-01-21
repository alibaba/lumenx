#!/bin/bash

# Mac 打包脚本 - 使用 PyInstaller 打包

set -e

echo "======================================"
echo "开始 Mac 打包流程"
echo "======================================"

# 先构建前端项目
echo "0. 开始构建前端项目..."
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

# 清理 .next 缓存以避免与开发模式冲突
echo "   清理 .next 缓存（避免与开发模式冲突）..."
rm -rf frontend/.next
echo ""

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 Python3，请先安装 Python3"
    exit 1
fi

# 检查并创建虚拟环境
echo "1. 检查 Python 虚拟环境..."
if [ ! -d ".venv" ]; then
    echo "   .venv 不存在，正在创建虚拟环境..."
    python3 -m venv .venv
    echo "   虚拟环境创建成功"
else
    echo "   .venv 已存在"
fi

# 激活虚拟环境
echo "2. 激活虚拟环境..."
source .venv/bin/activate

# 安装项目依赖
echo "3. 安装项目依赖..."
if [ -f "requirements.txt" ]; then
    pip install --upgrade pip
    pip install -r requirements.txt
    echo "   依赖安装完成"
else
    echo "   警告: 未找到 requirements.txt"
fi

# 检查并安装必要的打包工具
echo "4. 检查并安装打包工具..."
pip install --upgrade pyinstaller

# 清理之前的打包文件
echo "5. 清理旧的打包文件..."
rm -rf build dist dist_mac *.spec __pycache__
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 准备 FFmpeg
echo "5.5. 准备 FFmpeg..."
mkdir -p bin
if [ ! -f "bin/ffmpeg" ]; then
    if command -v ffmpeg &> /dev/null; then
        echo "   从系统路径复制 FFmpeg..."
        # 获取 ffmpeg 真实路径（解决符号链接问题）
        FFMPEG_PATH=$(which ffmpeg)
        cp "$FFMPEG_PATH" bin/
        echo "   已复制: $FFMPEG_PATH -> bin/ffmpeg"
    else
        echo "   错误: 未找到 FFmpeg。请安装 ffmpeg (brew install ffmpeg) 或手动下载并放置在 bin/ffmpeg"
        exit 1
    fi
else
    echo "   使用现有的 bin/ffmpeg"
fi
chmod +x bin/ffmpeg

# 使用 PyInstaller 打包
echo "6. 使用 PyInstaller 打包..."

# 检查图标文件是否存在
if [ -f "icon.icns" ]; then
    ICON_PARAM="--icon=icon.icns"
else
    ICON_PARAM=""
    echo "提示: 未找到 icon.icns，将使用默认图标"
fi

pyinstaller --clean --noconfirm \
    --name "LumenX Studio" \
    --windowed \
    $ICON_PARAM \
    --add-data "static:static" \
    --add-data "src:src" \
    --add-binary "bin/ffmpeg:." \
    --hidden-import=src \
    --hidden-import=src.apps \
    --hidden-import=src.apps.comic_gen \
    --hidden-import=src.apps.comic_gen.api \
    --hidden-import=uvicorn.logging \
    --hidden-import=uvicorn.loops \
    --hidden-import=uvicorn.loops.auto \
    --hidden-import=uvicorn.protocols \
    --hidden-import=uvicorn.protocols.http \
    --hidden-import=uvicorn.protocols.http.auto \
    --hidden-import=uvicorn.protocols.websockets \
    --hidden-import=uvicorn.protocols.websockets.auto \
    --hidden-import=uvicorn.lifespan \
    --hidden-import=uvicorn.lifespan.on \
    --hidden-import=webview \
    --hidden-import=starlette \
    --hidden-import=starlette.staticfiles \
    --hidden-import=fastapi \
    --hidden-import=pydantic \
    --hidden-import=dashscope \
    --hidden-import=oss2 \
    --hidden-import=alibabacloud_videoenhan20200320 \
    --hidden-import=alibabacloud_tea_openapi \
    --hidden-import=alibabacloud_tea_util \
    --hidden-import=yaml \
    --hidden-import=dotenv \
    --hidden-import=httptools \
    --hidden-import=uvloop \
    --hidden-import=requests \
    --hidden-import=multipart \
    --collect-all uvicorn \
    --collect-all fastapi \
    --collect-all starlette \
    --collect-all pydantic \
    main.py

# 复制打包结果到项目根目录
echo "7. 复制打包结果..."
mkdir -p dist_mac
cp -r dist/* dist_mac/

# 创建 DMG 安装包
echo "8. 创建 DMG 安装包..."

# 定义 DMG 文件名和路径
APP_NAME="LumenX Studio"
DMG_NAME="${APP_NAME}.dmg"
DMG_PATH="dist_mac/${DMG_NAME}"
APP_PATH="dist_mac/${APP_NAME}.app"

# 检查 .app 是否存在
if [ ! -d "$APP_PATH" ]; then
    echo "错误: 未找到 ${APP_NAME}.app"
    exit 1
fi

# 删除旧的 DMG 文件
if [ -f "$DMG_PATH" ]; then
    rm "$DMG_PATH"
fi

# 创建临时 DMG 目录
TMP_DMG_DIR="dist_mac/dmg_tmp"
rm -rf "$TMP_DMG_DIR"
mkdir -p "$TMP_DMG_DIR"

# 复制 .app 到临时目录
cp -R "$APP_PATH" "$TMP_DMG_DIR/"

# 复制安装脚本到临时目录
if [ -f "运行APP前_先点我安装.sh" ]; then
    cp "运行APP前_先点我安装.sh" "$TMP_DMG_DIR/"
    chmod +x "$TMP_DMG_DIR/运行APP前_先点我安装.sh"
    echo "   已添加安装脚本到 DMG"
else
    echo "   警告: 未找到 运行APP前_先点我安装.sh 安装脚本"
fi

# 创建 Applications 软链接（方便用户拖拽安装）
ln -s /Applications "$TMP_DMG_DIR/Applications"

# 使用 hdiutil 创建 DMG
echo "   正在生成 DMG 文件..."

# 先卸载可能存在的挂载
hdiutil detach "/Volumes/${APP_NAME}" 2>/dev/null || true

# 等待一下，确保资源释放
sleep 2

# 创建 DMG
hdiutil create -volname "${APP_NAME}" \
    -srcfolder "$TMP_DMG_DIR" \
    -ov -format UDZO \
    "$DMG_PATH"

if [ $? -eq 0 ]; then
    echo "   DMG 创建成功: $DMG_PATH"
else
    echo "   警告: DMG 创建失败，但 .app 文件已成功打包"
fi

# 清理临时目录
rm -rf "$TMP_DMG_DIR"

echo "======================================"
echo "打包完成！"
echo "输出目录: dist_mac/"
echo "App 文件: ${APP_NAME}.app"
echo "DMG 文件: ${DMG_NAME}"
echo "======================================"
