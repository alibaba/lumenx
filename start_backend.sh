#!/bin/bash

# 阿里云服务不走代理（避免PAC配置被Python忽略）
# macOS系统代理会被requests库读取，但PAC规则不会被解析
# 显式设置NO_PROXY确保阿里云域名直连
export NO_PROXY="*.aliyuncs.com,localhost,127.0.0.1"
export no_proxy="*.aliyuncs.com,localhost,127.0.0.1"

echo "========================================"
echo "Starting Backend (FastAPI)..."
echo "Port: 17177"
echo "Proxy Bypass: *.aliyuncs.com"
echo "========================================"

# 确保在项目根目录
cd "$(dirname "$0")"

# 启动 uvicorn
python -m uvicorn src.apps.comic_gen.api:app --reload --port 17177 --host 0.0.0.0

