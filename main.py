import os
import sys
import threading
import time
import webview
from datetime import datetime
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
path = os.path.expanduser("~/.lumen-x")
os.makedirs(path, exist_ok=True)
os.chdir(path)

# 配置日志文件路径
log_dir = os.path.join(path, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "app.log")


# 创建一个同时写入文件和控制台的类
class TeeOutput:
    def __init__(self, file_path, original_stream):
        self.file = open(file_path, 'a', encoding='utf-8')
        self.original = original_stream

    def write(self, message):
        self.file.write(message)
        self.file.flush()
        self.original.write(message)
        self.original.flush()

    def flush(self):
        self.file.flush()
        self.original.flush()

    def isatty(self):
        # 返回原始流的 isatty 状态
        return self.original.isatty() if hasattr(self.original, 'isatty') else False


# 保存原始的stdout和stderr
original_stdout = sys.stdout
original_stderr = sys.stderr

# 重定向标准输出和标准错误到文件和控制台
sys.stdout = TeeOutput(log_file, original_stdout)
sys.stderr = TeeOutput(log_file, original_stderr)

# 在重定向后导入其他模块
import uvicorn
from src.apps.comic_gen.api import app
from src.utils import setup_logging

import mimetypes
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

# 设置日志系统
setup_logging(log_file=log_file)


def run_server():
    app.mount("/static", StaticFiles(directory=
                                     os.path.join(cwd, "static"), html=True), name="static")

    # 直接传入 app 对象,而非字符串路径
    # 这样可以避免 PyArmor 混淆后字符串导入失败的问题
    # 注意: Windows 不支持 uvloop, 使用默认的 asyncio 事件循环
    uvicorn.run(app,
                host="127.0.0.1",
                port=17177,
                reload=False,
                log_level="info",
                )


def open_webview():
    # 等待服务器启动
    time.sleep(2)

    # 在 Windows 平台上检查并安装 WebView2 Runtime
    if sys.platform == 'win32':
        try:
            from src.utils.webview2_installer import ensure_webview2_runtime
            if not ensure_webview2_runtime():
                print("警告: WebView2 Runtime 未安装或安装失败")
                print("应用可能无法正常运行，请手动安装 Edge WebView2 Runtime")
                print("下载地址: https://developer.microsoft.com/microsoft-edge/webview2/")
                time.sleep(5)  # 给用户时间阅读提示
        except Exception as e:
            print(f"检查 WebView2 Runtime 时出错: {e}")
            print("将尝试继续启动...")

    # 创建 pywebview 窗口
    window = webview.create_window(
        title="LumenX Studio",
        url=f"http://127.0.0.1:17177/static/index.html?timestamp={datetime.now().timestamp()}",
        width=1280,
        height=800,
        resizable=True,
        fullscreen=False,
        min_size=(800, 600)
    )

    # 启动 webview(阻塞式调用)
    if sys.platform == 'win32':
        # gui='edgechromium': 使用 Edge Chromium 引擎(Windows 推荐),替代已弃用的 MSHTML
        webview.start(
            gui='edgechromium',
            private_mode=False,
            storage_path=os.path.expanduser("~/.lumen-x/webview_storage")
        )
    else:
        # private_mode=False: 禁用隐私模式,允许保存 cookies 和 localStorage
        # storage_path: 指定持久化存储路径,确保 localStorage 数据不会丢失
        webview.start(
            private_mode=False,
            storage_path=os.path.expanduser("~/.lumen-x/webview_storage")
        )

    # WebView 关闭后，退出整个进程
    os._exit(0)


if __name__ == "__main__":
    # 在后台线程启动服务器
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 在主线程打开 WebView
    open_webview()
