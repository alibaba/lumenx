#!/bin/bash

# Mac 打包脚本 - 使用 PyArmor 混淆 + PyInstaller 打包
# 支持通过参数 --no-obfuscation 跳过混淆步骤

set -e

echo "======================================"
echo "开始 Mac 打包流程"
echo "======================================"

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

# 检查并安装必要的打包工具
echo "1. 检查并安装打包工具..."
if [ "$NO_OBFUSCATION" = true ]; then
    pip3 install --upgrade pyinstaller
else
    pip3 install --upgrade pyinstaller pyarmor
fi

# 清理之前的打包文件
echo "2. 清理旧的打包文件..."
rm -rf build dist obfuscated dist_mac *.spec __pycache__
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

if [ "$NO_OBFUSCATION" = true ]; then
    # 不混淆模式：直接在当前目录打包
    echo "3. 跳过代码混淆步骤"
    WORK_DIR="."
    ADD_DATA_STATIC="static:static"
    ADD_DATA_PRESET="src:src"
    HOOKS_DIR=".pyinstaller-hooks"
    ICON_PATH="icon.icns"
    HIDDEN_IMPORT_SRC="--hidden-import=src --hidden-import=src.apps --hidden-import=src.apps.comic_gen --hidden-import=src.apps.comic_gen.api"
else
    # 混淆模式：创建混淆目录
    echo "3. 使用 PyArmor 混淆代码..."
    mkdir -p obfuscated
    
    # 混淆所有 Python 文件到同一目录结构
    # 先混淆 src 目录，输出到 obfuscated/ (不是 obfuscated/src)
    pyarmor gen -O obfuscated -r src/
    
    # 混淆 main.py
    pyarmor gen -O obfuscated main.py
    
    # 复制静态资源
    echo "4. 复制静态资源..."
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
    echo "4. 使用 PyInstaller 打包..."
else
    echo "5. 使用 PyInstaller 打包..."
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
    --add-data "$ADD_DATA_PRESET" \
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
    echo "5. 复制打包结果..."
    mkdir -p dist_mac
    cp -r dist/* dist_mac/
else
    echo "6. 复制打包结果..."
    cd ..
    mkdir -p dist_mac
    cp -r obfuscated/dist/* dist_mac/
fi

echo "======================================"
echo "打包完成！"
echo "输出目录: dist_mac/"
if [ "$NO_OBFUSCATION" = true ]; then
    echo "模式: 无混淆"
else
    echo "模式: PyArmor 混淆"
fi
echo "======================================"
