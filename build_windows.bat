@echo off
REM Windows 打包脚本 - 使用 PyArmor 混淆 + PyInstaller 打包

echo ======================================
echo 开始 Windows 打包流程
echo ======================================

REM 检查 Python 环境
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 未找到 Python，请先安装 Python
    exit /b 1
)

REM 检查并安装必要的打包工具
echo 1. 检查并安装打包工具...
pip install --upgrade pyinstaller pyarmor

REM 清理之前的打包文件
echo 2. 清理旧的打包文件...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist obfuscated rmdir /s /q obfuscated
if exist *.spec del /q *.spec
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"

REM 创建混淆目录
echo 3. 使用 PyArmor 混淆代码...
mkdir obfuscated

REM 混淆 src 目录下的所有 Python 文件
pyarmor gen -O obfuscated/src -r src/

REM 复制 main.py 到混淆目录并混淆
copy main.py obfuscated\
pyarmor gen -O obfuscated obfuscated\main.py

REM 复制静态资源
echo 4. 复制静态资源...
xcopy /E /I /Y static obfuscated\static

REM 复制其他必要文件
if exist requirements.txt copy requirements.txt obfuscated\
if exist .env copy .env obfuscated\

REM 复制 style_presets.json
if exist src\apps\comic_gen\style_presets.json (
    if not exist obfuscated\src\src\apps\comic_gen mkdir obfuscated\src\src\apps\comic_gen
    copy src\apps\comic_gen\style_presets.json obfuscated\src\src\apps\comic_gen\
)

REM 进入混淆目录
cd obfuscated

REM 使用 PyInstaller 打包
echo 5. 使用 PyInstaller 打包...

REM 检查图标文件是否存在
if exist ..\icon.ico (
    set ICON_PARAM=--icon=..\icon.ico
) else (
    set ICON_PARAM=
    echo 提示: 未找到 icon.ico，将使用默认图标
)

pyinstaller --clean --noconfirm ^
    --name "云创AI漫剧" ^
    --windowed ^
    %ICON_PARAM% ^
    --add-data "static;static" ^
    --add-data "src\src\apps\comic_gen\style_presets.json;src\apps\comic_gen" ^
    --additional-hooks-dir=..\.pyinstaller-hooks ^
    --hidden-import=uvicorn.logging ^
    --hidden-import=uvicorn.loops ^
    --hidden-import=uvicorn.loops.auto ^
    --hidden-import=uvicorn.protocols ^
    --hidden-import=uvicorn.protocols.http ^
    --hidden-import=uvicorn.protocols.http.auto ^
    --hidden-import=uvicorn.protocols.websockets ^
    --hidden-import=uvicorn.protocols.websockets.auto ^
    --hidden-import=uvicorn.lifespan ^
    --hidden-import=uvicorn.lifespan.on ^
    --hidden-import=webview ^
    --hidden-import=starlette ^
    --hidden-import=starlette.staticfiles ^
    --hidden-import=fastapi ^
    --hidden-import=pydantic ^
    --hidden-import=dashscope ^
    --hidden-import=oss2 ^
    --hidden-import=alibabacloud_videoenhan20200320 ^
    --hidden-import=alibabacloud_tea_openapi ^
    --hidden-import=alibabacloud_tea_util ^
    --hidden-import=yaml ^
    --hidden-import=dotenv ^
    --hidden-import=httptools ^
    --hidden-import=uvloop ^
    --hidden-import=requests ^
    --hidden-import=multipart ^
    --collect-all uvicorn ^
    --collect-all fastapi ^
    --collect-all starlette ^
    --collect-all pydantic ^
    main.py

REM 复制打包结果到项目根目录
echo 6. 复制打包结果...
cd ..
if not exist dist_windows mkdir dist_windows
xcopy /E /I /Y obfuscated\dist\* dist_windows\

echo ======================================
echo 打包完成！
echo 输出目录: dist_windows\
echo ======================================
pause
