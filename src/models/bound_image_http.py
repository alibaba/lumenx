"""Bounded image protocol JSON and anonymous private downloads shared by opt-in providers."""

from contextlib import closing
from pathlib import Path
import inspect
import json
from typing import TYPE_CHECKING, Callable

import requests

from .provider_result_fetch import iter_anonymous_result_chunks
from .provider_tasks import ProviderBindingError, ProviderFetchError, ProviderQueryError, ProviderSubmitError

if TYPE_CHECKING:
    from .provider_tasks import ProviderHandle

ImageReceiptWriter = Callable[["ProviderHandle"], object]
_MAX_JSON_BYTES = 256 * 1024


def _read_json_object(
    response: requests.Response,
    error: type[ProviderSubmitError] | type[ProviderQueryError],
    *, reject_duplicate_keys: bool = False,
) -> dict:
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise error("protocol")
    declared = response.headers.get("Content-Length")
    length = None
    if declared is not None:
        if not declared.isascii() or not declared.isdigit() or len(declared) > 20:
            raise error("protocol")
        length = int(declared)
        if not 0 < length <= _MAX_JSON_BYTES:
            raise error("protocol")
    data = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        if (
            len(data) + len(chunk) > _MAX_JSON_BYTES
            or length is not None
            and len(data) + len(chunk) > length
        ):
            raise error("protocol")
        data.extend(chunk)
    if not data or length is not None and len(data) != length:
        raise error("protocol")

    def reject_non_json_constant(_value):
        raise ValueError

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        result = json.loads(
            data.decode("utf-8"), parse_constant=reject_non_json_constant,
            object_pairs_hook=unique_object if reject_duplicate_keys else None,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise error("protocol") from None
    if not isinstance(result, dict):
        raise error("protocol")
    return result


def _read_json(
    response: requests.Response,
    error: type[ProviderSubmitError] | type[ProviderQueryError],
) -> dict:
    result = _read_json_object(response, error)
    if not isinstance(result.get("output"), dict):
        raise error("protocol")
    if result.get("code"):
        raise error("protocol")
    return result["output"]


def _request_json_response(
    method: str, url: str, headers: dict, payload: dict | None, *, legacy_output: bool,
) -> tuple[int, dict]:
    error = ProviderSubmitError if method == "POST" else ProviderQueryError
    try:
        # A fresh session for each operation prevents response cookies from
        # crossing submit/query boundaries. Default adapters perform no retries.
        with requests.Session() as session:
            session.trust_env = False
            session.auth = None
            session.cookies.clear()
            session.headers.clear()
            session.headers["Accept-Encoding"] = "identity"
            with session.request(
                method,
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(10, 120 if method == "POST" else 30),
                allow_redirects=False,
            ) as response:
                if legacy_output:
                    if response.status_code != 200:
                        raise error("http", status_code=response.status_code)
                    return response.status_code, _read_json(response, error)
                return response.status_code, _read_json_object(
                    response, error, reject_duplicate_keys=True,
                )
    except requests.RequestException:
        raise error("transport") from None


def request_json(
    method: str, url: str, headers: dict, payload: dict | None = None,
) -> dict:
    """Keep the original 200/output envelope contract for sync callers."""
    return _request_json_response(method, url, headers, payload, legacy_output=True)[1]


def request_json_envelope(
    method: str, url: str, headers: dict, payload: dict | None = None,
) -> tuple[int, dict]:
    """One isolated HTTP operation; the explicit adapter owns status/envelope rules."""
    if method not in {"POST", "GET"}:
        raise ValueError("Image task transport requires POST or GET")
    return _request_json_response(method, url, headers, payload, legacy_output=False)


def validate_receipt_writer(on_submitted: ImageReceiptWriter) -> None:
    """Reject deferred writers before any paid request."""
    if not callable(on_submitted) or any(
        check(candidate)
        for candidate in (on_submitted, getattr(on_submitted, "__call__", None))
        for check in (
            inspect.iscoroutinefunction, inspect.isgeneratorfunction, inspect.isasyncgenfunction,
        )
    ):
        raise ProviderBindingError("Submission requires a synchronous receipt writer")


def write_submitted_handle(handle: "ProviderHandle", on_submitted: ImageReceiptWriter) -> None:
    """Return only after the caller's synchronous persistence has succeeded."""
    acknowledgement = on_submitted(handle)
    if acknowledgement is not None:
        if inspect.iscoroutine(acknowledgement) or inspect.isgenerator(acknowledgement):
            acknowledgement.close()
        raise TypeError("Receipt writer must return None synchronously")


def download_private(url: str, output: Path, max_bytes: int) -> None:
    created = False
    try:
        with output.open("xb") as target:
            created = True
            with closing(
                iter_anonymous_result_chunks(url, max_bytes=max_bytes)
            ) as chunks:
                for chunk in chunks:
                    target.write(chunk)
    except BaseException as error:
        if created:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                error.add_note("Private image cleanup also failed")
        if isinstance(error, OSError):
            raise ProviderFetchError("local") from None
        raise
