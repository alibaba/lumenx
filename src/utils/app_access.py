"""Minimal app access boundary for explicit remote LumenX deployments.

The default local mode is intentionally disabled. Remote mode is an opt-in
process-local guard for the desktop/remote bridge; it is not an account system.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Any
from urllib.parse import urlsplit, urlunsplit
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send


SESSION_COOKIE_NAME = "__Host-lumenx_session"
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_MAX_SESSIONS = 128
_MIN_TOKEN_BYTES = 32
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SAFE_PUBLIC_METHODS = {"GET", "HEAD"}
_CORS_METHODS = "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
_CORS_HEADERS = "Authorization, Content-Type, Range, X-LumenX-Environment, X-LumenX-Product"
_EXPOSE_HEADERS = "Content-Disposition, Content-Range, Accept-Ranges"
_MAX_LOGIN_BODY_BYTES = 4096


class _BodyTooLarge(Exception):
    pass


@dataclass
class AppAccessConfig:
    mode: str = "local"
    token: str | None = field(default=None, repr=False)
    allowed_origins: tuple[str, ...] = ()
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    max_sessions: int = DEFAULT_MAX_SESSIONS
    cookie_secure: bool = True

    @property
    def auth_required(self) -> bool:
        return self.mode == "remote"


@dataclass
class AppAccessState:
    config: AppAccessConfig = field(default_factory=AppAccessConfig)
    sessions: dict[str, float] = field(default_factory=dict)

    def configure(self, config: AppAccessConfig) -> None:
        self.config = config
        self.sessions.clear()

    def cleanup(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        expired = [sid for sid, expires_at in self.sessions.items() if expires_at <= current]
        for sid in expired:
            self.sessions.pop(sid, None)
        while len(self.sessions) > self.config.max_sessions:
            oldest = min(self.sessions, key=self.sessions.get)
            self.sessions.pop(oldest, None)

    def create_session(self) -> str:
        self.cleanup()
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = time.time() + self.config.session_ttl_seconds
        self.cleanup()
        return sid

    def validate_session(self, sid: str | None) -> bool:
        if not sid:
            return False
        self.cleanup()
        expires_at = self.sessions.get(sid)
        if expires_at is None or expires_at <= time.time():
            self.sessions.pop(sid, None)
            return False
        return True

    def revoke_session(self, sid: str | None) -> None:
        if sid:
            self.sessions.pop(sid, None)


def _constant_time_equal(left: str, right: str) -> bool:
    try:
        return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except UnicodeError:
        return False


def _normalize_mode(value: str | None) -> str:
    mode = (value or "local").strip().lower()
    if mode not in {"local", "remote"}:
        raise ValueError("LUMENX_ACCESS_MODE must be 'local' or 'remote'")
    return mode


def _normalize_origin(value: str) -> str:
    origin = value.strip().rstrip("/")
    if origin in {"", "*", "null"}:
        raise ValueError("LUMENX_ACCESS_ALLOWED_ORIGINS must contain explicit http(s) origins")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LUMENX_ACCESS_ALLOWED_ORIGINS must contain explicit http(s) origins")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("LUMENX_ACCESS_ALLOWED_ORIGINS must not include credentials, paths, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _split_origins(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.split(",")
    else:
        raw = list(value)
    origins = []
    for item in raw:
        normalized = _normalize_origin(str(item))
        if normalized not in origins:
            origins.append(normalized)
    return tuple(origins)


def _validate_positive_int(value: int | None, default: int, name: str) -> int:
    candidate = default if value is None else value
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ValueError(f"{name} must be a positive integer")
    if candidate <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return candidate


def _validate_token(mode: str, token: str | None) -> str | None:
    if mode != "remote":
        return token.strip() if isinstance(token, str) and token.strip() else None
    candidate = token.strip() if isinstance(token, str) else ""
    if len(candidate.encode("utf-8")) < _MIN_TOKEN_BYTES:
        raise ValueError("LUMENX_ACCESS_TOKEN must be at least 32 bytes when LUMENX_ACCESS_MODE=remote")
    return candidate


def resolve_app_access_config(
    *,
    mode: str | None = None,
    token: str | None = None,
    allowed_origins: str | Iterable[str] | None = None,
    session_ttl_seconds: int | None = None,
    max_sessions: int | None = None,
    cookie_secure: bool | None = None,
) -> AppAccessConfig:
    resolved_mode = _normalize_mode(mode if mode is not None else os.getenv("LUMENX_ACCESS_MODE"))
    resolved_token = _validate_token(
        resolved_mode,
        token if token is not None else os.getenv("LUMENX_ACCESS_TOKEN"),
    )
    env_origins = os.getenv("LUMENX_ACCESS_ALLOWED_ORIGINS")
    resolved_cookie_secure = True if cookie_secure is None else bool(cookie_secure)
    if resolved_mode == "remote" and not resolved_cookie_secure:
        raise ValueError("LUMENX_ACCESS_COOKIE_SECURE must remain true in remote mode")
    return AppAccessConfig(
        mode=resolved_mode,
        token=resolved_token,
        allowed_origins=_split_origins(allowed_origins if allowed_origins is not None else env_origins),
        session_ttl_seconds=_validate_positive_int(
            session_ttl_seconds,
            DEFAULT_SESSION_TTL_SECONDS,
            "LUMENX_ACCESS_SESSION_TTL_SECONDS",
        ),
        max_sessions=_validate_positive_int(
            max_sessions,
            DEFAULT_MAX_SESSIONS,
            "LUMENX_ACCESS_MAX_SESSIONS",
        ),
        cookie_secure=resolved_cookie_secure,
    )


def configure_app_access(
    state: AppAccessState,
    *,
    mode: str | None = None,
    token: str | None = None,
    allowed_origins: str | Iterable[str] | None = None,
    session_ttl_seconds: int | None = None,
    max_sessions: int | None = None,
    cookie_secure: bool | None = None,
) -> AppAccessConfig:
    config = resolve_app_access_config(
        mode=mode,
        token=token,
        allowed_origins=allowed_origins,
        session_ttl_seconds=session_ttl_seconds,
        max_sessions=max_sessions,
        cookie_secure=cookie_secure,
    )
    state.configure(config)
    return config


def is_access_required(scope: Scope) -> bool:
    state = scope.get("app") and getattr(scope["app"].state, "app_access", None)
    return bool(state and state.config.auth_required)


def is_request_authenticated(scope: Scope) -> bool:
    state_obj = scope.get("state")
    if isinstance(state_obj, dict):
        return bool(state_obj.get("lumenx_authenticated"))
    return bool(getattr(state_obj, "lumenx_authenticated", False)) if state_obj is not None else False


def _header_map(scope: Scope) -> dict[str, str]:
    return {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers", [])}


def _cookies(headers: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in headers.get("cookie", "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _same_origin(scope: Scope) -> str:
    scheme = scope.get("scheme") or "http"
    server = scope.get("server") or ("testserver", 80)
    host, port = server[0], server[1]
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" if port == default else f"{scheme}://{host}:{port}"


def _request_origin(scope: Scope, headers: dict[str, str]) -> str:
    host = headers.get("host")
    if host:
        return f"{scope.get('scheme') or 'http'}://{host}".rstrip("/")
    return _same_origin(scope).rstrip("/")


def _parsed_origin(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
        # Accessing port validates malformed/non-numeric port components too.
        parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")), parsed.hostname


def _origin_allowed(origin: str | None, scope: Scope, headers: dict[str, str], config: AppAccessConfig) -> bool:
    if not origin:
        return False
    parsed_origin = _parsed_origin(origin)
    if parsed_origin is None:
        return False
    normalized, origin_host = parsed_origin
    if normalized == _request_origin(scope, headers) or normalized in config.allowed_origins:
        return True
    if config.mode == "local":
        request_origin = _parsed_origin(_request_origin(scope, headers))
        if request_origin and origin_host == request_origin[1]:
            return True
    return False


def _is_public_static_path(path: str) -> bool:
    return path in {"/", "/favicon.ico"} or path == "/static" or path.startswith("/static/")


def _is_public_health(method: str, path: str) -> bool:
    return method in _SAFE_PUBLIC_METHODS and path == "/health"


def _is_login_path(method: str, path: str) -> bool:
    return method == "POST" and path == "/auth/session"


def _is_session_discovery(method: str, path: str) -> bool:
    return method == "GET" and path == "/auth/session"


def _is_logout_path(method: str, path: str) -> bool:
    return method == "DELETE" and path == "/auth/session"


def _is_true_preflight(method: str, headers: dict[str, str]) -> bool:
    return method == "OPTIONS" and bool(headers.get("access-control-request-method"))


def _public_without_auth(method: str, path: str, headers: dict[str, str]) -> bool:
    return (
        _is_public_health(method, path)
        or _is_session_discovery(method, path)
        or _is_login_path(method, path)
        or _is_true_preflight(method, headers)
        or (method in _SAFE_PUBLIC_METHODS and _is_public_static_path(path))
    )


def _unauthorized() -> JSONResponse:
    return JSONResponse({"code": "access_required", "detail": "Authentication required"}, status_code=401)


def _forbidden(code: str = "access_forbidden") -> JSONResponse:
    return JSONResponse({"code": code, "detail": "Request is not allowed"}, status_code=403)


def _ok_session(auth_required: bool, authenticated: bool) -> JSONResponse:
    return JSONResponse({"auth_required": auth_required, "authenticated": authenticated})


def _mark_scope(scope: Scope, *, authenticated: bool, method: str | None = None) -> None:
    state_obj = scope.setdefault("state", {})
    if isinstance(state_obj, dict):
        state_obj["lumenx_authenticated"] = authenticated
        if method:
            state_obj["lumenx_auth_method"] = method
    else:
        setattr(state_obj, "lumenx_authenticated", authenticated)
        if method:
            setattr(state_obj, "lumenx_auth_method", method)


class LumenXAccessMiddleware:
    def __init__(self, app: ASGIApp, *, state: AppAccessState, external_bearer_authenticator: Callable[[str], Any] | None = None) -> None:
        self.app = app
        self.state = state
        self.external_bearer_authenticator = external_bearer_authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        config = self.state.config
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "/")
        headers = _header_map(scope)
        origin = headers.get("origin")
        if not config.auth_required:
            if _is_true_preflight(method, headers):
                response = Response(status_code=200 if _origin_allowed(origin, scope, headers, config) else 403)
                await self._send_response(scope, response, send, origin=origin)
                return
            if path == "/auth/session" and method in {"GET", "POST", "DELETE"}:
                await self._send_response(scope, _ok_session(False, True), send, origin=origin)
                return
            await self._call_app(scope, receive, send, origin=origin)
            return

        fetch_site = headers.get("sec-fetch-site")
        auth = self._authenticate(headers)
        if auth[0]:
            _mark_scope(scope, authenticated=True, method=auth[1])
        else:
            _mark_scope(scope, authenticated=False)

        if _is_true_preflight(method, headers):
            response = Response(status_code=200 if _origin_allowed(origin, scope, headers, config) else 403)
            await self._send_response(scope, response, send, origin=origin)
            return

        if fetch_site == "cross-site" and not _public_without_auth(method, path, headers):
            await self._send_response(scope, _forbidden("cross_site_request"), send, origin=origin)
            return

        if _is_session_discovery(method, path):
            await self._send_response(scope, _ok_session(True, auth[0]), send, origin=origin)
            return

        if _is_login_path(method, path):
            await self._handle_login(scope, receive, send, headers=headers, origin=origin)
            return

        if _is_logout_path(method, path):
            if not auth[0]:
                await self._send_response(scope, _unauthorized(), send, origin=origin)
                return
            if auth[1] == "cookie" and not _origin_allowed(origin, scope, headers, config):
                await self._send_response(scope, _forbidden("bad_origin"), send, origin=origin)
                return
            self.state.revoke_session(_cookies(headers).get(SESSION_COOKIE_NAME))
            response = _ok_session(True, False)
            response.delete_cookie(
                SESSION_COOKIE_NAME,
                path="/",
                secure=self.state.config.cookie_secure,
                httponly=True,
                samesite="strict",
            )
            await self._send_response(scope, response, send, origin=origin)
            return

        if not auth[0]:
            if _public_without_auth(method, path, headers):
                await self._call_app(scope, receive, send, origin=origin)
                return
            await self._send_response(scope, _unauthorized(), send, origin=origin)
            return

        if auth[1] == "cookie" and method in _MUTATING_METHODS and not _origin_allowed(origin, scope, headers, config):
            await self._send_response(scope, _forbidden("bad_origin"), send, origin=origin)
            return

        await self._call_app(scope, receive, send, origin=origin)

    def _authenticate(self, headers: dict[str, str]) -> tuple[bool, str | None]:
        token = self.state.config.token or ""
        authorization = headers.get("authorization") or ""
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not value:
                return False, None
            if _constant_time_equal(value, token):
                return True, "bearer"
            if self.external_bearer_authenticator is not None:
                try:
                    self.external_bearer_authenticator(value)
                    return True, "external-bearer"
                except Exception:
                    pass
            return False, None
        sid = _cookies(headers).get(SESSION_COOKIE_NAME)
        if self.state.validate_session(sid):
            return True, "cookie"
        return False, None

    async def _read_body(self, receive: Receive) -> bytes:
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > _MAX_LOGIN_BODY_BYTES:
                raise _BodyTooLarge
            chunks.append(chunk)
            more = bool(message.get("more_body", False))
        return b"".join(chunks)

    async def _handle_login(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        headers: dict[str, str],
        origin: str | None,
    ) -> None:
        if not _origin_allowed(origin, scope, headers, self.state.config):
            await self._send_response(scope, _forbidden("bad_origin"), send, origin=origin)
            return
        try:
            body = await self._read_body(receive)
        except _BodyTooLarge:
            await self._send_response(
                scope,
                JSONResponse({"code": "request_too_large", "detail": "Login request is too large"}, status_code=413),
                send,
                origin=origin,
            )
            return
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            await self._send_response(scope, _unauthorized(), send, origin=origin)
            return
        provided = payload.get("token") if isinstance(payload, dict) else None
        expected = self.state.config.token or ""
        if not isinstance(provided, str) or not _constant_time_equal(provided, expected):
            await self._send_response(scope, _unauthorized(), send, origin=origin)
            return
        sid = self.state.create_session()
        response = _ok_session(True, True)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            sid,
            max_age=self.state.config.session_ttl_seconds,
            httponly=True,
            secure=self.state.config.cookie_secure,
            samesite="strict",
            path="/",
        )
        await self._send_response(scope, response, send, origin=origin)

    async def _call_app(self, scope: Scope, receive: Receive, send: Send, *, origin: str | None) -> None:
        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._apply_cors(scope, message, origin)
            await send(message)

        await self.app(scope, receive, wrapped_send)

    async def _send_response(self, scope: Scope, response: Response, send: Send, *, origin: str | None) -> None:
        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._apply_cors(scope, message, origin)
            await send(message)

        await response(scope, self._empty_receive, wrapped_send)

    async def _empty_receive(self) -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    def _apply_cors(self, scope: Scope, message: Message, origin: str | None) -> None:
        headers = MutableHeaders(scope=message)
        for key in (
            "access-control-allow-origin",
            "access-control-allow-credentials",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "access-control-expose-headers",
        ):
            if key in headers:
                del headers[key]
        request_headers = _header_map(scope)
        path = str(scope.get("path") or "/")
        if self.state.config.auth_required or path == "/auth/session":
            headers["Cache-Control"] = "no-store"
            headers["Pragma"] = "no-cache"
        if origin and _origin_allowed(origin, scope, request_headers, self.state.config):
            requested_headers = request_headers.get("access-control-request-headers") or _CORS_HEADERS
            headers["Access-Control-Allow-Origin"] = origin.rstrip("/")
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Access-Control-Allow-Methods"] = _CORS_METHODS
            headers["Access-Control-Allow-Headers"] = requested_headers
            headers["Access-Control-Expose-Headers"] = _EXPOSE_HEADERS
            headers.add_vary_header("Origin")
