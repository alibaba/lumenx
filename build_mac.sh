#!/bin/bash

# Mac 打包脚本 - 使用 PyArmor 混淆 + PyInstaller 打包
# 支持通过参数 --no-obfuscation 跳过混淆步骤

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
echo ""

# 检查是否跳过混淆
NO_OBFUSCATION=false
if [[ "$1" == "--no-obfuscation" ]]; then
    NO_OBFUSCATION=true
    echo "注意: 将跳过代码混淆步骤"
fi

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
if [ "$NO_OBFUSCATION" = true ]; then
    pip install --upgrade pyinstaller
else
    pip install --upgrade pyinstaller pyarmor
fi

# 清理之前的打包文件
echo "5. 清理旧的打包文件..."
rm -rf build dist obfuscated dist_mac *.spec __pycache__
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

if [ "$NO_OBFUSCATION" = true ]; then
    # 不混淆模式：直接在当前目录打包
    echo "6. 跳过代码混淆步骤"
    WORK_DIR="."
    ADD_DATA_STATIC="static:static"
    ADD_DATA_PRESET="src:src"
    HOOKS_DIR=".pyinstaller-hooks"
    ICON_PATH="icon.icns"
    HIDDEN_IMPORT_SRC="--hidden-import=src --hidden-import=src.apps --hidden-import=src.apps.comic_gen --hidden-import=src.apps.comic_gen.api"
else
    # 混淆模式：创建混淆目录
    echo "6. 使用 PyArmor 混淆代码..."
    mkdir -p obfuscated
    
    # 混淆所有 Python 文件到同一目录结构
    # 先混淆 src 目录，输出到 obfuscated/ (不是 obfuscated/src)
    pyarmor gen -O obfuscated -r src/
    
    # 混淆 main.py
    pyarmor gen -O obfuscated main.py
    
    # 复制静态资源
    echo "7. 复制静态资源..."
    cp -r static obfuscated/
    
    # 复制其他必要文件
    if [ -f "requirements.txt" ]; then
        cp requirements.txt obfuscated/
    fi
    
    if [ -f ".env" ]; then
        cp .env obfuscated/
    fi
    
    # 复制 style_presets.json
    if [ -f "src/apps/comic_gen/style_presets.json" ]; then
        mkdir -p obfuscated/src/apps/comic_gen/
        cp src/apps/comic_gen/style_presets.json obfuscated/src/apps/comic_gen/
    fi
    
    # 进入混淆目录
    cd obfuscated
    WORK_DIR="obfuscated"
    ADD_DATA_STATIC="static:static"
    ADD_DATA_PRESET="src/apps/comic_gen/style_presets.json:src/apps/comic_gen"
    HOOKS_DIR="../.pyinstaller-hooks"
    ICON_PATH="../icon.icns"
    HIDDEN_IMPORT_SRC=""
fi

# 使用 PyInstaller 打包
if [ "$NO_OBFUSCATION" = true ]; then
    echo "7. 使用 PyInstaller 打包..."
else
    echo "8. 使用 PyInstaller 打包..."
fi

# 检查图标文件是否存在
if [ -f "$ICON_PATH" ]; then
    ICON_PARAM="--icon=$ICON_PATH"
else
    ICON_PARAM=""
    echo "提示: 未找到 icon.icns，将使用默认图标"
fi

pyinstaller --clean --noconfirm \
    --name "云创AI漫剧" \
    --windowed \
    $ICON_PARAM \
    --add-data "$ADD_DATA_STATIC" \
    --add-data "src:src" \
    --additional-hooks-dir=$HOOKS_DIR \
    $HIDDEN_IMPORT_SRC \
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
if [ "$NO_OBFUSCATION" = true ]; then
    echo "8. 复制打包结果..."
    mkdir -p dist_mac
    cp -r dist/* dist_mac/
else
    echo "9. 复制打包结果..."
    cd ..
    mkdir -p dist_mac
    cp -r obfuscated/dist/* dist_mac/
fi

# 创建 DMG 安装包
if [ "$NO_OBFUSCATION" = true ]; then
    echo "9. 创建 DMG 安装包..."
else
    echo "10. 创建 DMG 安装包..."
fi

# 定义 DMG 文件名和路径
APP_NAME="云创AI漫剧"
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

# 创建 Applications 软链接（方便用户拖拽安装）
ln -s /Applications "$TMP_DMG_DIR/Applications"

# 使用 hdiutil 创建 DMG
echo "   正在生成 DMG 文件..."
hdiutil create -volname "${APP_NAME}" \
    -srcfolder "$TMP_DMG_DIR" \
    -ov -format UDZO \
    "$DMG_PATH"

# 清理临时目录
rm -rf "$TMP_DMG_DIR"

echo "   DMG 创建完成: $DMG_PATH"

echo "======================================"
echo "打包完成！"
echo "输出目录: dist_mac/"
echo "App 文件: ${APP_NAME}.app"
echo "DMG 文件: ${DMG_NAME}"
if [ "$NO_OBFUSCATION" = true ]; then
    echo "模式: 无混淆"
else
    echo "模式: PyArmor 混淆"
fi
echo "======================================"
