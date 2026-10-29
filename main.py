import argparse
import json
import logging
import mimetypes
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from src.lumenx_service_identity import LUMENX_PROTOCOL_VERSION, LUMENX_SERVICE_NAME


def get_application_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent


def configure_packaged_paths() -> Path:
    application_path = get_application_path()
    if getattr(sys, "frozen", False):
        resources_path = application_path.parent.parent / "Resources"
        for path in (resources_path, application_path):
            path_str = str(path)
            if path.exists() and path_str not in sys.path:
                sys.path.insert(0, path_str)
    return application_path


def default_user_data_dir() -> Path:
    return Path(os.environ.get("LUMENX_DATA_DIR", "~/.lumen-x")).expanduser()


def _validate_backend_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("backend URL must be an absolute http(s) URL")
    return url


def _backend_service_base_url(url: str) -> str:
    """Return the backend service base for either a root URL or static entry URL."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    normalized_path = path.rstrip("/") or "/"
    if normalized_path.endswith("/static/index.html"):
        path = normalized_path[: -len("/static/index.html")] or "/"
    elif normalized_path.endswith("/static"):
        path = normalized_path[: -len("/static")] or "/"
    if not path.endswith("/"):
        path = path + "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _backend_health_url(url: str) -> str:
    base = _backend_service_base_url(url)
    parsed = urlparse(base)
    path = (parsed.path or "/").rstrip("/") + "/health"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_health_url(url: str, *, timeout: float):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=timeout)


def _check_backend_health(url: str, *, timeout: float = 5) -> None:
    health_url = _backend_health_url(url)
    try:
        with _open_health_url(health_url, timeout=timeout) as response:
            status = getattr(response, "status", getattr(response, "code", None))
            if status != 200:
                raise RuntimeError(f"backend health returned HTTP {status}")
            content_type = ""
            if hasattr(response, "headers"):
                content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                raise RuntimeError("backend health did not return JSON")
            payload = json.loads(response.read(65536).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"backend health returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"backend health check failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("backend health check timed out") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("backend health did not return valid JSON") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("backend health is not ok")
    if payload.get("service") != LUMENX_SERVICE_NAME:
        raise RuntimeError("backend health identity mismatch")
    protocol_version = payload.get("protocol_version")
    if type(protocol_version) is not int or protocol_version != LUMENX_PROTOCOL_VERSION:
        raise RuntimeError("backend health protocol version mismatch")


def get_static_shell_dir() -> Path:
    return get_application_path() / "static"


def _validate_static_shell(static_dir: Path) -> Path:
    static_dir = Path(static_dir).resolve()
    if not static_dir.is_dir() or not (static_dir / "index.html").is_file():
        raise RuntimeError(f"desktop static app shell is missing: {static_dir / 'index.html'}")
    return static_dir


def create_local_server(
    *,
    data_dir: Path | str,
    host: str = "127.0.0.1",
    port: int = 17177,
    use_user_config: bool = True,
):
    """Build the owned local backend server for desktop local mode."""
    data_root = Path(data_dir).expanduser().resolve()
    output_dir = data_root / "output"

    from starlette.staticfiles import StaticFiles
    import uvicorn
    from src.apps.comic_gen.api import create_app

    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/javascript", ".js")

    app = create_app(
        data_dir=output_dir,
        user_data_dir=data_root if use_user_config else None,
        load_config=True,
        setup_logs=True,
        recover_orphan_tasks=True,
        warmup_demucs=False,
    )
    static_dir = _validate_static_shell(get_static_shell_dir())
    app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
    return uvicorn.Server(config)


def _open_webview(url: str, *, data_dir: Path | str | None = None) -> None:
    import webview

    storage_root = Path(data_dir).expanduser() if data_dir is not None else default_user_data_dir()
    webview.create_window(
        title="LumenX Studio",
        url=url,
        width=1280,
        height=800,
        resizable=True,
        fullscreen=False,
        min_size=(800, 600),
    )
    kwargs = {
        "private_mode": False,
        "storage_path": str(storage_root / "webview_storage"),
    }
    if sys.platform == "win32":
        try:
            from src.utils.webview2_installer import ensure_webview2_runtime

            ensure_webview2_runtime()
        except Exception as exc:
            print(f"检查 WebView2 Runtime 时出错: {exc}")
        kwargs["gui"] = "edgechromium"
    webview.start(**kwargs)


def _wait_for_server_start(
    server,
    thread: threading.Thread,
    timeout: float,
    *,
    health_url: str,
) -> None:
    deadline = time.monotonic() + timeout
    saw_started = False
    last_health_error: Exception | None = None
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            saw_started = True
            try:
                remaining = deadline - time.monotonic()
                _check_backend_health(health_url, timeout=min(1, max(0.1, remaining)))
                return
            except RuntimeError as exc:
                last_health_error = exc
        if not thread.is_alive():
            break
        time.sleep(0.05)
    server.should_exit = True
    if saw_started and last_health_error is not None:
        raise RuntimeError("local backend health check failed") from last_health_error
    raise RuntimeError("local backend failed to start")


def _stop_owned_server(
    server,
    thread: threading.Thread,
    *,
    join_timeout: float = 5,
    primary_error: BaseException | None = None,
) -> None:
    server.should_exit = True
    thread.join(timeout=join_timeout)
    if thread.is_alive():
        message = "owned local backend thread did not stop"
        if primary_error is not None:
            logging.getLogger(__name__).error("%s after primary error: %s", message, primary_error)
            return
        raise RuntimeError(message)


def run_desktop(
    *,
    backend_url: str | None = None,
    data_dir: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 17177,
    startup_timeout: float = 15,
) -> None:
    """Open the desktop shell against either an owned local backend or a remote URL."""
    if backend_url is not None:
        _validate_backend_url(backend_url)
    configure_packaged_paths()
    data_root = Path(data_dir).expanduser() if data_dir is not None else default_user_data_dir()

    if backend_url is not None:
        _check_backend_health(backend_url)
        _open_webview(backend_url, data_dir=data_root)
        return

    _validate_static_shell(get_static_shell_dir())
    server = create_local_server(
        data_dir=data_root, host=host, port=port,
        use_user_config=(data_dir is not None or bool(os.environ.get("LUMENX_DATA_DIR"))
                         or getattr(sys, "frozen", False)),
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    primary_error: BaseException | None = None
    try:
        url = f"http://{host}:{port}/static/index.html?timestamp={datetime.now().timestamp()}"
        _wait_for_server_start(server, server_thread, startup_timeout, health_url=url)
        _open_webview(url, data_dir=data_root)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _stop_owned_server(server, server_thread, primary_error=primary_error)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LumenX Studio desktop shell.")
    parser.add_argument(
        "--backend-url",
        help="Open an existing deployed LumenX app entry URL after checking backend health.",
    )
    parser.add_argument("--data-dir", type=Path, help="LumenX user data root.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17177)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    run_desktop(
        backend_url=args.backend_url,
        data_dir=args.data_dir,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
