from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class DownloadTooLargeError(RuntimeError):
    """Raised when a streamed download exceeds its configured byte limit."""


def create_retry_session(
    *,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=("HEAD", "GET", "OPTIONS"),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _iter_response_chunks(
    response: Any,
    *,
    chunk_size: int,
    max_bytes: int | None,
):
    total_bytes = 0
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        total_bytes += len(chunk)
        if max_bytes is not None and total_bytes > max_bytes:
            raise DownloadTooLargeError(f"Download exceeded {max_bytes} bytes")
        yield chunk


def stream_response_to_file(
    response: Any,
    output_path: str | Path,
    *,
    chunk_size: int = 1024 * 1024,
    max_bytes: int | None = None,
) -> str:
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=str(target_path.parent),
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temp_path = Path(output_file.name)
            for chunk in _iter_response_chunks(
                response,
                chunk_size=chunk_size,
                max_bytes=max_bytes,
            ):
                output_file.write(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temp_path, target_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return str(target_path)


def download_url_to_file(
    url: str,
    output_path: str | Path,
    *,
    session: requests.Session | None = None,
    timeout: float | tuple[float, float] = (30, 180),
    verify: bool = True,
    chunk_size: int = 1024 * 1024,
    max_bytes: int | None = None,
    max_attempts: int = 3,
    retry_sleep_base: float = 1.0,
) -> str:
    http = session or create_retry_session()
    close_http = session is None
    retryable_errors = (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.SSLError,
        requests.exceptions.Timeout,
    )

    try:
        for attempt in range(1, max_attempts + 1):
            response = None
            try:
                response = http.get(url, stream=True, timeout=timeout, verify=verify)
                response.raise_for_status()
                return stream_response_to_file(
                    response,
                    output_path,
                    chunk_size=chunk_size,
                    max_bytes=max_bytes,
                )
            except retryable_errors:
                if attempt >= max_attempts:
                    raise
                time.sleep(min(2 ** (attempt - 1) * retry_sleep_base, 4))
            finally:
                close_response = getattr(response, "close", None)
                if callable(close_response):
                    close_response()
    finally:
        if close_http:
            close_session = getattr(http, "close", None)
            if callable(close_session):
                close_session()

    raise RuntimeError(f"Failed to download {url}")


def download_url_to_bytes(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: float | tuple[float, float] = (30, 60),
    verify: bool = True,
    chunk_size: int = 1024 * 1024,
    max_bytes: int | None = None,
) -> tuple[bytes, Mapping[str, str]]:
    http = session or create_retry_session()
    close_http = session is None
    response = None

    try:
        response = http.get(url, stream=True, timeout=timeout, verify=verify)
        response.raise_for_status()

        content = bytearray()
        for chunk in _iter_response_chunks(
            response,
            chunk_size=chunk_size,
            max_bytes=max_bytes,
        ):
            content.extend(chunk)
        return bytes(content), response.headers
    finally:
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()
        if close_http:
            close_session = getattr(http, "close", None)
            if callable(close_session):
                close_session()
