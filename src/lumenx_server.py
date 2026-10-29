"""Run the shared LumenX app without a desktop GUI.

Example: python -m src.lumenx_server --data-dir /srv/lumenx --static-dir ./static
Provider settings live in the selected root's config.json. Remote access follows
the existing LUMENX_ACCESS_* contract; proxy trust is always explicit.
"""

from __future__ import annotations

import argparse
from ipaddress import ip_address
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uvicorn


def _proxy_ips(value: str) -> list[str]:
    try:
        return [str(ip_address(item.strip())) for item in value.split(",")]
    except ValueError as exc:
        raise ValueError("trusted proxy values must be explicit IP addresses, not networks or '*'") from exc


def create_server(
    *,
    data_dir: Path | str,
    host: str = "127.0.0.1",
    port: int = 17177,
    static_dir: Path | str | None = None,
    trusted_proxy_ips: str = "127.0.0.1",
) -> uvicorn.Server:
    """Configure one owned writer; runtime initialization waits for server startup."""
    data_root = Path(data_dir).expanduser().resolve()
    proxies = _proxy_ips(trusted_proxy_ips)
    static_root = Path(static_dir).expanduser().resolve() if static_dir is not None else None
    if static_root is not None:
        if not static_root.is_dir() or not (static_root / "index.html").is_file():
            raise ValueError(f"static app shell is missing: {static_root / 'index.html'}")
        if static_root.is_relative_to(data_root) or data_root.is_relative_to(static_root):
            raise ValueError("static shell and private data directories must not overlap")

    import uvicorn
    from starlette.responses import RedirectResponse
    from starlette.staticfiles import StaticFiles

    from src.apps.comic_gen.api import create_app

    app = create_app(
        data_dir=data_root / "output",
        user_data_dir=data_root,
        load_config=True,
        setup_logs=True,
        recover_orphan_tasks=True,
        warmup_demucs=False,
    )
    if static_root is not None:
        mimetypes.add_type("text/css", ".css")
        mimetypes.add_type("application/javascript", ".js")
        app.mount("/static", StaticFiles(directory=str(static_root), html=True), name="static")

        @app.get("/", include_in_schema=False)
        def open_static_shell() -> RedirectResponse:
            return RedirectResponse("/static/index.html")

    return uvicorn.Server(uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=1,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips=proxies,
        log_level="info",
    ))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LumenX without a desktop GUI.")
    parser.add_argument("--data-dir", type=Path, required=True, help="Persistent configuration, logs and output root.")
    parser.add_argument("--static-dir", type=Path, help="Optional production app shell, served at /static.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17177)
    parser.add_argument(
        "--trusted-proxy-ips", default="127.0.0.1",
        help="Comma-separated trusted proxy IP addresses; hostnames, networks and '*' are rejected.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    server = create_server(
        data_dir=args.data_dir,
        host=args.host,
        port=args.port,
        static_dir=args.static_dir,
        trusted_proxy_ips=args.trusted_proxy_ips,
    )
    try:
        server.run()
    except KeyboardInterrupt:
        # Uvicorn finishes lifespan cleanup before re-raising Ctrl+C.
        return
    if not server.started:
        # Supported Uvicorn versions can return after failed lifespan startup.
        raise SystemExit(3)


if __name__ == "__main__":
    main()
