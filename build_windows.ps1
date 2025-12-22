# Windows build script - PyInstaller packaging
# PowerShell version

Write-Host "======================================"
Write-Host "Starting Windows packaging process"
Write-Host "======================================"

# Check Python environment
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python not found, please install Python first" -ForegroundColor Red
    exit 1
}

# Check and create virtual environment
Write-Host "1. Checking Python virtual environment..."
if (-not (Test-Path ".venv")) {
    Write-Host "   .venv does not exist, creating virtual environment..."
    python -m venv .venv
    Write-Host "   Virtual environment created successfully"
} else {
    Write-Host "   .venv already exists"
}

# Activate virtual environment
Write-Host "2. Activating virtual environment..."
& ".venv\Scripts\Activate.ps1"

# Install project dependencies
Write-Host "3. Installing project dependencies..."
if (Test-Path "requirements.txt") {
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    Write-Host "   Dependencies installed successfully"
} else {
    Write-Host "   Warning: requirements.txt not found" -ForegroundColor Yellow
}

# Check and install necessary packaging tools
Write-Host "4. Checking and installing packaging tools..."
pip install --upgrade pyinstaller

# Clean previous packaging files
Write-Host "5. Cleaning old packaging files..."
if (Test-Path "build") { Remove-Item -Recurse -Force build }
if (Test-Path "dist") { Remove-Item -Recurse -Force dist }
if (Test-Path "dist_windows") { Remove-Item -Recurse -Force dist_windows }
Get-ChildItem -Filter "*.spec" | Remove-Item -Force
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# Package with PyInstaller
Write-Host "6. Packaging with PyInstaller..."

# Check if icon file exists
$iconParam = ""
if (Test-Path "icon.ico") {
    $iconParam = "--icon=icon.ico"
} else {
    Write-Host "Note: icon.ico not found, using default icon" -ForegroundColor Yellow
}

# Build PyInstaller command arguments
$pyinstallerArgs = @(
    "--clean",
    "--noconfirm",
    "--onefile",
    "--name", "TronComic",
    "--windowed",
    "--add-data", "static;static",
    "--add-data", "src;src",
    "--exclude-module", "uvloop",
    "--hidden-import=uvicorn.logging",
    "--hidden-import=uvicorn.loops",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols",
    "--hidden-import=uvicorn.protocols.http",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.protocols.http.h11_impl",
    "--hidden-import=uvicorn.protocols.websockets.wsproto_impl",
    "--hidden-import=uvicorn.lifespan",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=webview",
    "--hidden-import=winreg",
    "--hidden-import=urllib.request",
    "--hidden-import=tempfile",
    "--hidden-import=subprocess",
    "--hidden-import=starlette",
    "--hidden-import=starlette.staticfiles",
    "--hidden-import=fastapi",
    "--hidden-import=pydantic",
    "--hidden-import=dashscope",
    "--hidden-import=oss2",
    "--hidden-import=alibabacloud_videoenhan20200320",
    "--hidden-import=alibabacloud_tea_openapi",
    "--hidden-import=alibabacloud_tea_util",
    "--hidden-import=yaml",
    "--hidden-import=dotenv",
    "--hidden-import=httptools",
    "--hidden-import=requests",
    "--hidden-import=multipart",
    "--collect-all", "uvicorn",
    "--collect-all", "fastapi",
    "--collect-all", "starlette",
    "--collect-all", "pydantic",
    "main.py"
)

# If icon parameter exists, add to argument list
if ($iconParam) {
    $pyinstallerArgs = @(
        "--clean",
        "--noconfirm",
        "--onefile",
        "--name", "TronComic",
        "--windowed",
        $iconParam,
        "--add-data", "static;static",
        "--add-data", "src;src",
        "--exclude-module", "uvloop",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.protocols.http.h11_impl",
        "--hidden-import=uvicorn.protocols.websockets.wsproto_impl",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=webview",
        "--hidden-import=winreg",
        "--hidden-import=urllib.request",
        "--hidden-import=tempfile",
        "--hidden-import=subprocess",
        "--hidden-import=starlette",
        "--hidden-import=starlette.staticfiles",
        "--hidden-import=fastapi",
        "--hidden-import=pydantic",
        "--hidden-import=dashscope",
        "--hidden-import=oss2",
        "--hidden-import=alibabacloud_videoenhan20200320",
        "--hidden-import=alibabacloud_tea_openapi",
        "--hidden-import=alibabacloud_tea_util",
        "--hidden-import=yaml",
        "--hidden-import=dotenv",
        "--hidden-import=httptools",
        "--hidden-import=requests",
        "--hidden-import=multipart",
        "--collect-all", "uvicorn",
        "--collect-all", "fastapi",
        "--collect-all", "starlette",
        "--collect-all", "pydantic",
        "main.py"
    )
}

# Execute PyInstaller
pyinstaller @pyinstallerArgs

# Copy packaging results to project root
Write-Host "7. Copying packaging results..."
if (-not (Test-Path "dist_windows")) {
    New-Item -ItemType Directory -Path dist_windows -Force | Out-Null
}
Copy-Item -Path dist\* -Destination dist_windows\ -Recurse -Force

Write-Host "======================================"
Write-Host "Packaging complete!" -ForegroundColor Green
Write-Host "Output directory: dist_windows\"
Write-Host "======================================"
Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
