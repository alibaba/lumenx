import os
import sys
import threading
import time
import webview

# 保存原始工作目录
if getattr(sys, 'frozen', False):
    # 打包后的环境
    application_path = sys._MEIPASS
    # 将打包后的 Resources 目录添加到 Python 路径，PyInstaller 通常将数据文件放在这里
    resources_path = os.path.join(os.path.dirname(os.path.dirname(application_path)), 'Resources')
    if os.path.exists(resources_path) and resources_path not in sys.path:
        sys.path.insert(0, resources_path)
    # 也添加 _MEIPASS 本身
    if application_path not in sys.path:
        sys.path.insert(0, application_path)
else:
    # 开发环境
    application_path = os.path.dirname(os.path.abspath(__file__))

cwd = application_path

from starlette.staticfiles import StaticFiles

# 切换到用户数据目录
path = os.path.expanduser("~/.tron/comic")
os.makedirs(path, exist_ok=True)
os.chdir(path)

import uvicorn
from src.apps.comic_gen.api import app

def run_server():
    app.mount("/static", StaticFiles(directory=
                                    os.path.join(cwd, "static"), html=True), name="static")
    
    # 直接传入 app 对象，而非字符串路径
    # 这样可以避免 PyArmor 混淆后字符串导入失败的问题
    uvicorn.run(app,
                host="127.0.0.1",
                port=8000,
                reload=False,
                loop="uvloop",
                http="httptools",
                log_level="info",
                )

def open_webview():
    # 等待服务器启动
    time.sleep(2)
    
    # 创建 pywebview 窗口
    window = webview.create_window(
        title="云创 AI 漫剧",
        url="http://127.0.0.1:8000/static/index.html",
        width=1280,
        height=800,
        resizable=True,
        fullscreen=True,
        min_size=(800, 600)
    )
    
    # 启动 webview（阻塞式调用）
    webview.start()
    
    # WebView 关闭后，退出整个进程
    os._exit(0)

if __name__ == "__main__":
    # 在后台线程启动服务器
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # 在主线程打开 WebView
    open_webview()
