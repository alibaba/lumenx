"""Private credential bindings for provider receipt recovery.

This module stores non-secret metadata only. Callers must pass already-resolved
secrets explicitly; the resolver never reads environment or user config.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import hmac
import json
import os
import secrets
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ContextManager, Iterator
from urllib.parse import urlsplit

from ..utils.mulerun_profile import (
    MuleRunProfileError,
    snapshot_mulerun_profile_to_temp_home_with_fingerprint,
)
from .provider_tasks import (
    ProviderBindingError,
    ProviderHandle,
    ProviderScope,
    QueryCapability,
    ResolvedCredential,
)
from ..utils.json_storage import (
    CorruptStorageError,
    DirectoryWriterLock,
    StorageConflictError,
    StorageError,
    atomic_write_json,
    read_json,
)


SCHEMA_VERSION = 1
CLI_SCHEMA_VERSION = 2
SOURCE_KEY = "DASHSCOPE_API_KEY"
MULEROUTER_HTTP_SOURCE_KEY = "MULEROUTER_API_KEY"
MULEROUTER_HTTP_BASE_URL = "https://api.mulerouter.ai"
MULERUN_TOKEN_SOURCE_KEY = "MULERUN_TOKEN"
MULERUN_PROFILE_SOURCE_KEY = "MULERUN_PROFILE_FINGERPRINT"
_CLI_SOURCE_KEYS = {MULERUN_TOKEN_SOURCE_KEY, MULERUN_PROFILE_SOURCE_KEY}
_LOGIN_SITES = {"sg", "cn"}
_STUDIO_SITES = {"mulerouter", "mulerun"}
STORE_FILENAME = "provider_credentials.json"
_MISSING = object()


class ProviderCredentialBindingError(ProviderBindingError):
    """The requested credential binding is outside this resolver's contract."""


class ProviderCredentialResolutionError(ProviderCredentialBindingError):
    """A historical receipt may be recoverable after restoring credentials."""

    def __init__(self, kind: str, *, state: str = "needs_credentials"):
        self.kind = kind
        self.state = state
        super().__init__(f"Provider credential resolution requires action ({state}:{kind})")


@dataclass(frozen=True)
class MuleRunCliRouteIdentity:
    """Non-secret MuleRun CLI routing identity persisted with CLI bindings."""

    schema_version: int
    login_site: str
    login_base_url: str
    studio_site: str
    studio_base_url: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ProviderCredentialBindingError("Unsupported MuleRun route schema")
        _validate_route_site(self.login_site, "MuleRun login site")
        _validate_route_site(self.studio_site, "MuleRun studio site")
        if self.login_site not in _LOGIN_SITES:
            raise ProviderCredentialBindingError("Unsupported MuleRun login site")
        if self.studio_site not in _STUDIO_SITES:
            raise ProviderCredentialBindingError("Unsupported MuleRun studio site")
        _validate_route_url(self.login_base_url, "MuleRun login base URL")
        _validate_route_url(self.studio_base_url, "MuleRun studio base URL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "login_site": self.login_site,
            "login_base_url": self.login_base_url,
            "studio_site": self.studio_site,
            "studio_base_url": self.studio_base_url,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "MuleRunCliRouteIdentity":
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "login_site",
            "login_base_url",
            "studio_site",
            "studio_base_url",
        }:
            raise ProviderCredentialBindingError("Invalid MuleRun route identity")
        return cls(**value)


@dataclass(frozen=True)
class ResolvedMuleRunTokenCredential:
    scope: ProviderScope
    credential_ref: str
    routes: MuleRunCliRouteIdentity
    secret: str = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_dict(),
            "credential_ref": self.credential_ref,
            "routes": self.routes.to_dict(),
            "source_key": MULERUN_TOKEN_SOURCE_KEY,
        }


@dataclass(frozen=True)
class ResolvedMuleRunProfileCredential:
    scope: ProviderScope
    credential_ref: str
    routes: MuleRunCliRouteIdentity
    profile_home: Path = field(repr=False, compare=False)
    profile_fingerprint: str = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_dict(),
            "credential_ref": self.credential_ref,
            "routes": self.routes.to_dict(),
            "source_key": MULERUN_PROFILE_SOURCE_KEY,
        }


@dataclass(frozen=True)
class _Record:
    credential_ref: str
    scope: ProviderScope
    source_key: str
    key_fingerprint: str
    cli_routes: MuleRunCliRouteIdentity | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "credential_ref": self.credential_ref,
            "scope": self.scope.to_dict(),
            "source_key": self.source_key,
            "key_fingerprint": self.key_fingerprint,
        }
        if self.cli_routes is not None:
            payload["cli_routes"] = self.cli_routes.to_dict()
        return payload


@dataclass(frozen=True)
class _StoreData:
    salt: str
    records: tuple[_Record, ...]

    def to_dict(self) -> dict[str, Any]:
        schema_version = (
            CLI_SCHEMA_VERSION
            if any(record.cli_routes is not None for record in self.records)
            else SCHEMA_VERSION
        )
        return {
            "schema_version": schema_version,
            "salt": self.salt,
            "records": [record.to_dict() for record in self.records],
        }


class ProviderCredentialStore:
    """Bind supported provider scope and key material to opaque local UUID refs."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        writer_lock: DirectoryWriterLock,
        runtime_lock: ContextManager[Any],
    ):
        if not isinstance(writer_lock, DirectoryWriterLock):
            raise StorageConflictError("Provider credential store requires a writer lease")
        if not callable(getattr(runtime_lock, "__enter__", None)) or not callable(
            getattr(runtime_lock, "__exit__", None)
        ):
            raise StorageConflictError("Provider credential store requires a shared runtime lock")
        self.data_dir = Path(data_dir).resolve()
        self.path = self.data_dir / STORE_FILENAME
        self._writer_lock = writer_lock
        self._runtime_lock = runtime_lock
        self._failed = False

    def bind(
        self,
        scope: ProviderScope,
        secret: str,
        *,
        source_key: str = SOURCE_KEY,
    ) -> ResolvedCredential:
        """Persist or reuse a private binding before a paid submit path."""
        self._validate_scope_source_pair(scope, source_key)
        ResolvedCredential(scope, str(uuid.uuid4()), secret)
        with self._runtime_lock:
            try:
                self._ensure_usable()
                data = self._load_or_empty()
                fingerprint = _fingerprint(data.salt, secret)
                for record in data.records:
                    if (
                        record.scope == scope
                        and record.source_key == source_key
                        and record.cli_routes is None
                        and hmac.compare_digest(record.key_fingerprint, fingerprint)
                    ):
                        return ResolvedCredential(scope, record.credential_ref, secret)
                record = _Record(
                    credential_ref=str(uuid.uuid4()),
                    scope=scope,
                    source_key=source_key,
                    key_fingerprint=fingerprint,
                )
                next_data = _StoreData(data.salt, data.records + (record,))
                self._save(next_data)
                return ResolvedCredential(scope, record.credential_ref, secret)
            except StorageError:
                self._failed = True
                raise

    def resolve_binding(
        self, scope: ProviderScope, credential_ref: str, secret: str, *, source_key: str = SOURCE_KEY,
    ) -> ResolvedCredential:
        """Resolve an existing accepted binding without creating a new record."""
        with self._runtime_lock:
            try:
                self._ensure_usable()
                self._validate_scope_source_pair(scope, source_key)
                if not _is_valid_secret(secret):
                    raise ProviderCredentialResolutionError("invalid_secret")
                data = self._load_existing()
                record = self._matching_record(data, credential_ref)
                if record.scope != scope:
                    raise ProviderCredentialResolutionError("scope_mismatch")
                if record.source_key != source_key:
                    raise ProviderCredentialResolutionError("source_key_mismatch")
                if record.cli_routes is not None:
                    raise ProviderCredentialResolutionError("source_key_mismatch")
                if not hmac.compare_digest(record.key_fingerprint, _fingerprint(data.salt, secret)):
                    raise ProviderCredentialResolutionError("fingerprint_mismatch")
                return ResolvedCredential(scope, credential_ref, secret)
            except ProviderCredentialResolutionError:
                raise
            except StorageError:
                self._failed = True
                raise

    def bind_mulerun_token(
        self,
        scope: ProviderScope,
        token: str,
        *,
        routes: MuleRunCliRouteIdentity,
    ) -> ResolvedMuleRunTokenCredential:
        self._validate_mulerun_cli_scope(scope, MULERUN_TOKEN_SOURCE_KEY, routes)
        if not _is_valid_secret(token):
            raise ProviderCredentialBindingError("Invalid provider credential")
        with self._runtime_lock:
            try:
                self._ensure_usable()
                data = self._load_or_empty()
                fingerprint = _fingerprint_cli(data.salt, scope, MULERUN_TOKEN_SOURCE_KEY, routes, token)
                for record in data.records:
                    if (
                        record.scope == scope
                        and record.source_key == MULERUN_TOKEN_SOURCE_KEY
                        and record.cli_routes == routes
                        and hmac.compare_digest(record.key_fingerprint, fingerprint)
                    ):
                        return ResolvedMuleRunTokenCredential(scope, record.credential_ref, routes, token)
                record = _Record(
                    credential_ref=str(uuid.uuid4()),
                    scope=scope,
                    source_key=MULERUN_TOKEN_SOURCE_KEY,
                    key_fingerprint=fingerprint,
                    cli_routes=routes,
                )
                self._save(_StoreData(data.salt, data.records + (record,)))
                return ResolvedMuleRunTokenCredential(scope, record.credential_ref, routes, token)
            except StorageError:
                self._failed = True
                raise

    def resolve_mulerun_token(
        self,
        scope: ProviderScope,
        credential_ref: str,
        token: str,
    ) -> ResolvedMuleRunTokenCredential:
        if not _is_valid_secret(token):
            raise ProviderCredentialResolutionError("invalid_secret")
        with self._runtime_lock:
            try:
                self._ensure_usable()
                data = self._load_existing()
                record = self._matching_record(data, credential_ref)
                self._validate_resolved_cli_record(record, scope, MULERUN_TOKEN_SOURCE_KEY)
                assert record.cli_routes is not None
                fingerprint = _fingerprint_cli(data.salt, scope, MULERUN_TOKEN_SOURCE_KEY, record.cli_routes, token)
                if not hmac.compare_digest(record.key_fingerprint, fingerprint):
                    raise ProviderCredentialResolutionError("fingerprint_mismatch")
                return ResolvedMuleRunTokenCredential(scope, credential_ref, record.cli_routes, token)
            except ProviderCredentialResolutionError:
                raise
            except StorageError:
                self._failed = True
                raise

    @contextmanager
    def bind_mulerun_profile(
        self,
        scope: ProviderScope,
        profile_home: str | Path,
        *,
        routes: MuleRunCliRouteIdentity,
    ) -> Iterator[ResolvedMuleRunProfileCredential]:
        self._validate_mulerun_cli_scope(scope, MULERUN_PROFILE_SOURCE_KEY, routes)
        with self._runtime_lock:
            try:
                self._ensure_usable()
                self._load_or_empty()
            except StorageError:
                self._failed = True
                raise
        snapshot_context = snapshot_mulerun_profile_to_temp_home_with_fingerprint(profile_home)
        try:
            snapshot = snapshot_context.__enter__()
        except MuleRunProfileError as exc:
            raise ProviderCredentialBindingError("Invalid MuleRun profile") from exc
        try:
            if not _snapshot_has_profile_files(snapshot.home):
                raise ProviderCredentialBindingError("Invalid MuleRun profile")
            with self._runtime_lock:
                try:
                    self._ensure_usable()
                    data = self._load_or_empty()
                    fingerprint = _fingerprint_cli(
                        data.salt, scope, MULERUN_PROFILE_SOURCE_KEY, routes, snapshot.fingerprint,
                    )
                    resolved = None
                    for record in data.records:
                        if (
                            record.scope == scope
                            and record.source_key == MULERUN_PROFILE_SOURCE_KEY
                            and record.cli_routes == routes
                            and hmac.compare_digest(record.key_fingerprint, fingerprint)
                        ):
                            resolved = ResolvedMuleRunProfileCredential(
                                scope, record.credential_ref, routes, snapshot.home, snapshot.fingerprint,
                            )
                            break
                    if resolved is None:
                        record = _Record(
                            credential_ref=str(uuid.uuid4()),
                            scope=scope,
                            source_key=MULERUN_PROFILE_SOURCE_KEY,
                            key_fingerprint=fingerprint,
                            cli_routes=routes,
                        )
                        self._save(_StoreData(data.salt, data.records + (record,)))
                        resolved = ResolvedMuleRunProfileCredential(
                            scope, record.credential_ref, routes, snapshot.home, snapshot.fingerprint,
                        )
                except StorageError:
                    self._failed = True
                    raise
            yield resolved
        finally:
            snapshot_context.__exit__(None, None, None)

    @contextmanager
    def resolve_mulerun_profile(
        self,
        scope: ProviderScope,
        credential_ref: str,
        profile_home: str | Path,
    ) -> Iterator[ResolvedMuleRunProfileCredential]:
        with self._runtime_lock:
            try:
                self._ensure_usable()
                data = self._load_existing()
                record = self._matching_record(data, credential_ref)
                self._validate_resolved_cli_record(record, scope, MULERUN_PROFILE_SOURCE_KEY)
            except ProviderCredentialResolutionError:
                raise
            except StorageError:
                self._failed = True
                raise
        snapshot_context = snapshot_mulerun_profile_to_temp_home_with_fingerprint(profile_home)
        try:
            snapshot = snapshot_context.__enter__()
        except MuleRunProfileError as exc:
            raise ProviderCredentialResolutionError("invalid_profile") from exc
        try:
            if not _snapshot_has_profile_files(snapshot.home):
                raise ProviderCredentialResolutionError("invalid_profile")
            with self._runtime_lock:
                try:
                    self._ensure_usable()
                    data = self._load_existing()
                    record = self._matching_record(data, credential_ref)
                    self._validate_resolved_cli_record(record, scope, MULERUN_PROFILE_SOURCE_KEY)
                    assert record.cli_routes is not None
                    fingerprint = _fingerprint_cli(
                        data.salt, scope, MULERUN_PROFILE_SOURCE_KEY, record.cli_routes, snapshot.fingerprint,
                    )
                    if not hmac.compare_digest(record.key_fingerprint, fingerprint):
                        raise ProviderCredentialResolutionError("fingerprint_mismatch")
                    resolved = ResolvedMuleRunProfileCredential(
                        scope, credential_ref, record.cli_routes, snapshot.home, snapshot.fingerprint,
                    )
                except ProviderCredentialResolutionError:
                    raise
                except StorageError:
                    self._failed = True
                    raise
            yield resolved
        finally:
            snapshot_context.__exit__(None, None, None)

    def resolve_handle(self, handle: ProviderHandle, secret: str) -> ResolvedCredential:
        """Resolve a historical handle using the handle's own stored scope."""
        if (
            not isinstance(handle, ProviderHandle)
            or handle.scope is None
            or handle.credential_ref is None
            or handle.query_capability != QueryCapability.BY_TASK_ID
        ):
            raise ProviderCredentialResolutionError("missing_handle_binding")
        return self.resolve_binding(
            handle.scope,
            handle.credential_ref,
            secret,
            source_key=self._source_key_for_scope(handle.scope),
        )

    def _ensure_usable(self) -> None:
        if self._failed:
            raise StorageError("Provider credential store failed; recreate it before reuse")
        self._assert_owned()
        self._reject_symlink()

    def _assert_owned(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            root_fd = os.open(self.data_dir, flags)
        except OSError as exc:
            raise StorageConflictError("Provider credential store requires an owned data directory") from exc
        try:
            self._writer_lock.assert_owned_for(root_fd)
        finally:
            os.close(root_fd)

    def _reject_symlink(self) -> None:
        try:
            current = self.path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StorageError("Cannot inspect provider credential metadata") from exc
        if stat.S_ISLNK(current.st_mode):
            raise StorageError("Provider credential metadata must not be a symlink")
        if not stat.S_ISREG(current.st_mode):
            raise StorageError("Provider credential metadata must be a regular file")

    def _load_or_empty(self) -> _StoreData:
        self._reject_symlink()
        raw = read_json(self.path, lambda: _MISSING)
        if raw is _MISSING:
            return _StoreData(_encode_salt(secrets.token_bytes(32)), ())
        return _parse_store(raw)

    def _load_existing(self) -> _StoreData:
        self._reject_symlink()
        raw = read_json(self.path, lambda: _MISSING)
        if raw is _MISSING:
            raise ProviderCredentialResolutionError("record_missing")
        return _parse_store(raw)

    def _save(self, data: _StoreData) -> None:
        self._reject_symlink()
        payload = data.to_dict()
        _parse_store(payload)
        try:
            atomic_write_json(self.path, payload)
        except StorageError:
            self._failed = True
            raise

    @staticmethod
    def _matching_record(data: _StoreData, credential_ref: str) -> _Record:
        matches = [record for record in data.records if record.credential_ref == credential_ref]
        if not matches:
            raise ProviderCredentialResolutionError("record_missing")
        return matches[0]

    @staticmethod
    def _validate_resolved_cli_record(record: _Record, scope: ProviderScope, source_key: str) -> None:
        if record.scope != scope:
            raise ProviderCredentialResolutionError("scope_mismatch")
        if record.source_key != source_key:
            raise ProviderCredentialResolutionError("source_key_mismatch")
        if record.cli_routes is None:
            raise ProviderCredentialResolutionError("source_key_mismatch")
        ProviderCredentialStore._validate_mulerun_cli_scope(scope, source_key, record.cli_routes)

    @staticmethod
    def _source_key_for_scope(scope: ProviderScope) -> str:
        try:
            ProviderCredentialStore._validate_dashscope_scope(scope)
            return SOURCE_KEY
        except ProviderCredentialBindingError:
            pass
        try:
            ProviderCredentialStore._validate_mulerouter_http_scope(scope)
            return MULEROUTER_HTTP_SOURCE_KEY
        except ProviderCredentialBindingError as exc:
            raise ProviderCredentialResolutionError("unsupported_scope") from exc

    @staticmethod
    def _validate_scope_source_pair(scope: ProviderScope, source_key: str) -> None:
        if not isinstance(source_key, str):
            raise ProviderCredentialBindingError("Unsupported provider credential source")
        if source_key == SOURCE_KEY:
            ProviderCredentialStore._validate_dashscope_scope(scope)
            return
        if source_key == MULEROUTER_HTTP_SOURCE_KEY:
            ProviderCredentialStore._validate_mulerouter_http_scope(scope)
            return
        raise ProviderCredentialBindingError("Unsupported provider credential source")

    @staticmethod
    def _validate_dashscope_scope(scope: ProviderScope) -> None:
        if not isinstance(scope, ProviderScope):
            raise ProviderCredentialBindingError("Provider credential scope is required")
        if (
            scope.provider != "dashscope"
            or scope.backend != "dashscope"
            or scope.gateway not in {"dashscope", "dashscope_workspace"}
            or scope.account_identity is not None
        ):
            raise ProviderCredentialBindingError("Unsupported provider credential scope")
        try:
            from .wanx import _dashscope_scope_headers

            _dashscope_scope_headers(scope)
        except ProviderBindingError as exc:
            raise ProviderCredentialBindingError("Unsupported provider credential scope") from exc

    @staticmethod
    def _validate_mulerouter_http_scope(scope: ProviderScope) -> None:
        if not isinstance(scope, ProviderScope):
            raise ProviderCredentialBindingError("Provider credential scope is required")
        if (
            scope.provider != "mulerouter"
            or scope.backend != "mulerouter"
            or scope.gateway != "mulerouter_http"
            or scope.region is not None
            or scope.workspace_id is not None
            or scope.account_identity is not None
        ):
            raise ProviderCredentialBindingError("Unsupported provider credential scope")
        if scope.base_url != MULEROUTER_HTTP_BASE_URL:
            raise ProviderCredentialBindingError("Unsupported provider credential scope")

    @staticmethod
    def _validate_mulerun_cli_scope(
        scope: ProviderScope, source_key: str, routes: MuleRunCliRouteIdentity,
    ) -> None:
        if not isinstance(routes, MuleRunCliRouteIdentity):
            raise ProviderCredentialBindingError("MuleRun route identity is required")
        if not isinstance(scope, ProviderScope):
            raise ProviderCredentialBindingError("Provider credential scope is required")
        expected_gateway = {
            MULERUN_TOKEN_SOURCE_KEY: "mulerun_token",
            MULERUN_PROFILE_SOURCE_KEY: "mulerun_profile",
        }.get(source_key)
        if expected_gateway is None:
            raise ProviderCredentialBindingError("Unsupported provider credential source")
        if (
            scope.provider != "mulerouter"
            or scope.backend != "mulerun_cli"
            or scope.gateway != expected_gateway
            or scope.base_url != routes.studio_base_url
            or scope.region is not None
            or scope.workspace_id is not None
            or scope.account_identity is not None
        ):
            raise ProviderCredentialBindingError("Unsupported provider credential scope")


def _parse_store(raw: Any) -> _StoreData:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "salt", "records"}:
        raise CorruptStorageError("Invalid provider credential metadata")
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] not in {SCHEMA_VERSION, CLI_SCHEMA_VERSION}
    ):
        raise CorruptStorageError("Unsupported provider credential metadata schema")
    schema_version = raw["schema_version"]
    salt = raw["salt"]
    _decode_salt(salt)
    raw_records = raw["records"]
    if not isinstance(raw_records, list):
        raise CorruptStorageError("Invalid provider credential records")
    records = tuple(_parse_record(item, schema_version=schema_version) for item in raw_records)
    refs = [record.credential_ref for record in records]
    if len(refs) != len(set(refs)):
        raise CorruptStorageError("Duplicate provider credential reference")
    return _StoreData(salt, records)


def _parse_record(raw: Any, *, schema_version: int) -> _Record:
    if not isinstance(raw, dict):
        raise CorruptStorageError("Invalid provider credential record")
    has_cli_routes = "cli_routes" in raw
    expected_keys = {"credential_ref", "scope", "source_key", "key_fingerprint"}
    if has_cli_routes:
        expected_keys = expected_keys | {"cli_routes"}
    if set(raw) != expected_keys:
        raise CorruptStorageError("Invalid provider credential record")
    try:
        credential_ref = str(uuid.UUID(raw["credential_ref"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CorruptStorageError("Invalid provider credential reference") from exc
    try:
        scope = ProviderScope.from_dict(raw["scope"])
    except ProviderBindingError as exc:
        raise CorruptStorageError("Invalid provider credential scope") from exc
    source_key = raw["source_key"]
    if not isinstance(source_key, str):
        raise CorruptStorageError("Invalid provider credential source")
    if schema_version == SCHEMA_VERSION and (has_cli_routes or source_key in _CLI_SOURCE_KEYS):
        raise CorruptStorageError("Invalid provider credential record")
    cli_routes = None
    if source_key in _CLI_SOURCE_KEYS:
        if not has_cli_routes:
            raise CorruptStorageError("Invalid provider credential record")
        try:
            cli_routes = MuleRunCliRouteIdentity.from_dict(raw["cli_routes"])
            ProviderCredentialStore._validate_mulerun_cli_scope(scope, source_key, cli_routes)
        except ProviderBindingError as exc:
            raise CorruptStorageError("Invalid provider credential scope") from exc
    else:
        if has_cli_routes:
            raise CorruptStorageError("Invalid provider credential record")
        try:
            ProviderCredentialStore._validate_scope_source_pair(scope, source_key)
        except ProviderBindingError as exc:
            raise CorruptStorageError("Invalid provider credential scope") from exc
    fingerprint = raw["key_fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise CorruptStorageError("Invalid provider credential fingerprint")
    return _Record(credential_ref, scope, source_key, fingerprint, cli_routes)


def _encode_salt(value: bytes) -> str:
    if not isinstance(value, bytes) or len(value) != 32:
        raise StorageError("Provider credential salt must be 32 bytes")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_salt(value: Any) -> bytes:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise CorruptStorageError("Invalid provider credential salt")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise CorruptStorageError("Invalid provider credential salt") from exc
    if len(decoded) != 32 or _encode_salt(decoded) != value:
        raise CorruptStorageError("Invalid provider credential salt")
    return decoded


def _fingerprint(salt: str, secret: str) -> str:
    if not _is_valid_secret(secret):
        raise ProviderCredentialBindingError("Invalid provider credential")
    return hmac.digest(_decode_salt(salt), secret.encode("ascii"), "sha256").hex()


def _fingerprint_cli(
    salt: str,
    scope: ProviderScope,
    source_key: str,
    routes: MuleRunCliRouteIdentity,
    authority: str,
) -> str:
    if source_key == MULERUN_TOKEN_SOURCE_KEY:
        if not _is_valid_secret(authority):
            raise ProviderCredentialBindingError("Invalid provider credential")
    elif source_key == MULERUN_PROFILE_SOURCE_KEY:
        if not _is_hex64(authority):
            raise ProviderCredentialBindingError("Invalid provider credential")
    else:
        raise ProviderCredentialBindingError("Unsupported provider credential source")
    payload = {
        "authority": authority,
        "routes": routes.to_dict(),
        "scope": scope.to_dict(),
        "source_key": source_key,
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.digest(_decode_salt(salt), material, "sha256").hex()


def _is_valid_secret(secret: Any) -> bool:
    return (
        isinstance(secret, str)
        and bool(secret)
        and secret.isascii()
        and not any(ord(char) <= 32 or ord(char) >= 127 for char in secret)
    )


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)



def _snapshot_has_profile_files(home: Path) -> bool:
    profile = home / ".mulerun"
    try:
        return any(path.is_file() for path in profile.rglob("*"))
    except OSError:
        return False

def _validate_route_site(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value)
    ):
        raise ProviderCredentialBindingError(f"Invalid {label}")


def _validate_route_url(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value)
    ):
        raise ProviderCredentialBindingError(f"Invalid {label}")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.path in {"", "/"}
            and (parsed.port is None or parsed.port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ProviderCredentialBindingError(f"Invalid {label}")
