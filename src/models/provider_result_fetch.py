"""Anonymous bounded result streaming shared by explicitly bound providers."""

import inspect
from pathlib import Path
import sys
from typing import Callable, ContextManager

import requests

from .provider_tasks import (
    ProviderArtifact, ProviderFetchError, ProviderHandle, ProviderObservation,
    validate_fetch_binding,
)
from ..utils.json_storage import DirectoryWriterLock
from ..utils.media_versions import store_media_stream


def _reject_deferred_callback(callback) -> None:
    if callback is None:
        return
    target = getattr(callback, "__call__", callback)
    if (
        inspect.iscoroutinefunction(callback)
        or inspect.isgeneratorfunction(callback)
        or inspect.isasyncgenfunction(callback)
        or inspect.iscoroutinefunction(target)
        or inspect.isgeneratorfunction(target)
        or inspect.isasyncgenfunction(target)
    ):
        raise ValueError("Prepared callback must be synchronous")


def _close_deferred_result(value) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def iter_anonymous_result_chunks(result_url: str, *, max_bytes: int):
    """Stream a validated result URL without ambient identity or redirects."""
    try:
        with requests.Session() as session:
            session.trust_env = False
            session.auth = None
            session.cookies.clear()
            session.headers.clear()
            session.headers["Accept-Encoding"] = "identity"
            with session.get(
                result_url, stream=True, timeout=(10, 30), allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ProviderFetchError("http", status_code=response.status_code)
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ProviderFetchError("protocol")
                length = response.headers.get("Content-Length")
                if length is not None:
                    if not isinstance(length, str) or not length.isascii() or not length.isdigit():
                        raise ProviderFetchError("protocol")
                    # Bound conversion as well as the resulting number.
                    if len(length) > 20 or not 0 < int(length) <= max_bytes:
                        raise ProviderFetchError("size")
                    length = int(length)
                received = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    received += len(chunk)
                    if received > max_bytes or length is not None and received > length:
                        raise ProviderFetchError("size")
                    yield chunk
                if not received or length is not None and received != length:
                    raise ProviderFetchError("size")
    except requests.RequestException:
        raise ProviderFetchError("transport") from None


def fetch_provider_result(
    handle: ProviderHandle, observation: ProviderObservation, *,
    output_root: Path, output_ref: str, writer_lock: DirectoryWriterLock,
    storage_guard: Callable[[], ContextManager[None]], max_bytes: int,
    on_prepared: Callable[[ProviderArtifact], None] | None = None,
    publish_policy: str = "replace",
) -> ProviderArtifact:
    """Download a bound observation without ambient credentials or provider I/O."""
    _reject_deferred_callback(on_prepared)
    validate_fetch_binding(handle, observation)
    video_url = observation.video_url
    assert isinstance(video_url, str)

    stream = iter_anonymous_result_chunks(video_url, max_bytes=max_bytes)
    try:
        def prepared_callback(relative_path: str, digest: str, size: int) -> None:
            if on_prepared is not None:
                result = on_prepared(ProviderArtifact(handle, relative_path, digest, size))
                if result is not None:
                    _close_deferred_result(result)
                    raise ValueError("Prepared callback must return None synchronously")

        relative_path, digest, size = store_media_stream(
            stream, output_ref=output_ref, output_root=output_root,
            writer_lock=writer_lock, storage_guard=storage_guard, max_bytes=max_bytes,
            on_prepared=prepared_callback if on_prepared is not None else None,
            publish_policy=publish_policy,
        )
    finally:
        # Close the HTTP response even if local validation/write fails while
        # the generator is suspended. This never runs under storage_guard.
        failure = sys.exc_info()[1]
        try:
            stream.close()
        except Exception:
            if failure is None:
                raise
            failure.add_note("HTTP response cleanup also failed")
    return ProviderArtifact(handle, relative_path, digest, size)
