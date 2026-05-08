import os
from pathlib import Path


OUTPUT_DIR_ENV = "LUMENX_OUTPUT_DIR"


LUMENX_API_HOST_ENV = "LUMENX_API_HOST"
LUMENX_API_PORT_ENV = "LUMENX_API_PORT"

DEFAULT_LUMENX_API_HOST = "127.0.0.1"
DEFAULT_LUMENX_API_PORT = 18177


def get_api_host(default: str = DEFAULT_LUMENX_API_HOST) -> str:
    return (os.getenv(LUMENX_API_HOST_ENV) or default).strip() or default


def get_api_port(default: int = DEFAULT_LUMENX_API_PORT) -> int:
    raw_port = (os.getenv(LUMENX_API_PORT_ENV) or str(default)).strip()
    try:
        port = int(raw_port)
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


def get_api_base_url(host: str | None = None, port: int | None = None) -> str:
    resolved_host = host or get_api_host()
    if resolved_host in {"0.0.0.0", "::", "[::]"}:
        resolved_host = "127.0.0.1"
    resolved_port = port if port is not None else get_api_port()
    return f"http://{resolved_host}:{resolved_port}"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_output_root(default: str = "output", project_root: str | Path | None = None) -> Path:
    raw_path = (os.getenv(OUTPUT_DIR_ENV) or default).strip() or default
    output_root = Path(raw_path)
    if not output_root.is_absolute():
        base_root = Path(project_root).resolve() if project_root else Path.cwd()
        output_root = base_root / output_root
    return output_root.resolve()
