"""MuleRun CLI credential authority bindings preserve exact non-secret routes."""

import json
import base64
import hmac
import uuid
from threading import RLock

import pytest

from src.models import provider_binding as provider_credentials
from src.models.provider_binding import MuleRunCliRouteIdentity
from src.models.provider_tasks import ProviderScope
from src.utils.mulerun_profile import MuleRunProfileError
from src.utils.json_storage import DirectoryWriterLock, StorageError, read_json

TOKEN = "mrtok_" + "t" * 40
OTHER_TOKEN = "mrtok_" + "u" * 40


def routes(studio_base_url="https://api.mulerouter.ai"):
    return MuleRunCliRouteIdentity(
        schema_version=1,
        login_site="sg",
        login_base_url="https://api.mulerun.com",
        studio_site="mulerouter",
        studio_base_url=studio_base_url,
    )


def token_scope(route=None):
    route = route or routes()
    return ProviderScope(
        provider="mulerouter",
        backend="mulerun_cli",
        gateway="mulerun_token",
        base_url=route.studio_base_url,
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def profile_scope(route=None):
    route = route or routes()
    return ProviderScope(
        provider="mulerouter",
        backend="mulerun_cli",
        gateway="mulerun_profile",
        base_url=route.studio_base_url,
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def store_for(root, lease):
    return provider_credentials.ProviderCredentialStore(root, writer_lock=lease, runtime_lock=RLock())


def read_store(root):
    return read_json(root / "provider_credentials.json", dict)


def make_profile(root, token_text="profile-secret"):
    home = root / "profile"
    profile = home / ".mulerun"
    profile.mkdir(parents=True)
    (profile / "token.json").write_text(token_text, encoding="utf-8")
    return home


def test_token_binding_writes_schema2_cli_record_and_preserves_legacy_records(tmp_path):
    dashscope = ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope",
        base_url="https://dashscope.aliyuncs.com",
        region="cn-beijing",
        workspace_id=None,
        account_identity=None,
    )
    route = routes()
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        legacy = store.bind(dashscope, "sk-test-" + "a" * 44)
        cli = store.bind_mulerun_token(token_scope(route), TOKEN, routes=route)

    stored = read_store(tmp_path)
    assert stored["schema_version"] == 2
    legacy_record, cli_record = stored["records"]
    assert legacy_record == {
        "credential_ref": legacy.credential_ref,
        "scope": dashscope.to_dict(),
        "source_key": "DASHSCOPE_API_KEY",
        "key_fingerprint": legacy_record["key_fingerprint"],
    }
    assert cli_record == {
        "credential_ref": cli.credential_ref,
        "scope": token_scope(route).to_dict(),
        "source_key": "MULERUN_TOKEN",
        "key_fingerprint": cli_record["key_fingerprint"],
        "cli_routes": route.to_dict(),
    }
    serialized = json.dumps(stored, sort_keys=True)
    assert TOKEN not in serialized
    assert "profile-secret" not in serialized


def test_token_resolve_returns_persisted_routes_and_rejects_changed_token(tmp_path):
    route = routes()
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        bound = store.bind_mulerun_token(token_scope(route), TOKEN, routes=route)
        restored = store.resolve_mulerun_token(token_scope(route), bound.credential_ref, TOKEN)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_mulerun_token(token_scope(route), bound.credential_ref, OTHER_TOKEN)

    assert restored.scope == token_scope(route)
    assert restored.credential_ref == bound.credential_ref
    assert restored.secret == TOKEN
    assert restored.routes == route
    assert raised.value.kind == "fingerprint_mismatch"


def test_token_resolve_cannot_replace_persisted_routes(tmp_path):
    original = routes("https://api.mulerouter.ai")
    changed = routes("https://api.mulerouter.example")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        bound = store.bind_mulerun_token(token_scope(original), TOKEN, routes=original)
        restored = store.resolve_mulerun_token(token_scope(original), bound.credential_ref, TOKEN)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_mulerun_token(token_scope(changed), bound.credential_ref, TOKEN)

    assert restored.routes == original
    assert raised.value.kind == "scope_mismatch"


def test_generic_binding_rejects_cli_sources_before_write(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError):
            store_for(tmp_path, lease).bind(token_scope(), TOKEN, source_key="MULERUN_TOKEN")
    assert not (tmp_path / "provider_credentials.json").exists()


def test_schema1_rejects_cli_shape_or_source(tmp_path):
    route = routes()
    salt = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    payload = {
        "schema_version": 1,
        "salt": salt,
        "records": [
            {
                "credential_ref": str(uuid.uuid4()),
                "scope": token_scope(route).to_dict(),
                "source_key": "MULERUN_TOKEN",
                "key_fingerprint": hmac.digest(bytes(range(32)), b"x", "sha256").hex(),
                "cli_routes": route.to_dict(),
            }
        ],
    }
    (tmp_path / "provider_credentials.json").write_text(json.dumps(payload), encoding="utf-8")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).resolve_mulerun_token(token_scope(route), payload["records"][0]["credential_ref"], TOKEN)


def test_schema1_rejects_cli_source_without_cli_routes(tmp_path):
    route = routes()
    salt = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    payload = {
        "schema_version": 1,
        "salt": salt,
        "records": [
            {
                "credential_ref": str(uuid.uuid4()),
                "scope": token_scope(route).to_dict(),
                "source_key": "MULERUN_TOKEN",
                "key_fingerprint": hmac.digest(bytes(range(32)), b"x", "sha256").hex(),
            }
        ],
    }
    (tmp_path / "provider_credentials.json").write_text(json.dumps(payload), encoding="utf-8")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).resolve_mulerun_token(token_scope(route), payload["records"][0]["credential_ref"], TOKEN)


def test_cli_route_identity_rejects_unsafe_urls_and_sites():
    with pytest.raises(provider_credentials.ProviderCredentialBindingError):
        MuleRunCliRouteIdentity(
            schema_version=1,
            login_site="sg",
            login_base_url="https://user:pass@api.mulerun.com",
            studio_site="mulerouter",
            studio_base_url="https://api.mulerouter.ai",
        )
    with pytest.raises(provider_credentials.ProviderCredentialBindingError):
        MuleRunCliRouteIdentity(
            schema_version=1,
            login_site="sg",
            login_base_url="https://api.mulerun.com",
            studio_site="mule router",
            studio_base_url="https://api.mulerouter.ai",
        )


def test_profile_binding_uses_copied_snapshot_and_stores_no_profile_path_or_bytes(tmp_path):
    route = routes()
    source = make_profile(tmp_path, "profile-secret-v1")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with store.bind_mulerun_profile(profile_scope(route), source, routes=route) as bound:
            (source / ".mulerun" / "token.json").write_text("profile-secret-v2", encoding="utf-8")
            assert (bound.profile_home / ".mulerun" / "token.json").read_text(encoding="utf-8") == "profile-secret-v1"
            stored = read_store(tmp_path)
            serialized = json.dumps(stored, sort_keys=True)
            assert str(source) not in serialized
            assert "profile-secret-v1" not in serialized
            assert "profile-secret-v2" not in serialized
            assert bound.routes == route


def test_profile_resolve_requires_current_profile_to_match_original_digest(tmp_path):
    route = routes()
    source = make_profile(tmp_path, "profile-secret-v1")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with store.bind_mulerun_profile(profile_scope(route), source, routes=route) as bound:
            credential_ref = bound.credential_ref
        with store.resolve_mulerun_profile(profile_scope(route), credential_ref, source) as restored:
            assert restored.routes == route
            assert (restored.profile_home / ".mulerun" / "token.json").read_text(encoding="utf-8") == "profile-secret-v1"
        (source / ".mulerun" / "token.json").write_text("profile-secret-v2", encoding="utf-8")
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            with store.resolve_mulerun_profile(profile_scope(route), credential_ref, source):
                pass

    assert raised.value.kind == "fingerprint_mismatch"


def test_profile_source_key_cannot_use_token_scope(tmp_path):
    route = routes()
    source = make_profile(tmp_path)
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError):
            with store_for(tmp_path, lease).bind_mulerun_profile(token_scope(route), source, routes=route):
                pass



def test_corrupt_non_string_source_key_is_storage_error_not_typeerror(tmp_path):
    route = routes()
    payload = {
        "schema_version": 2,
        "salt": base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("="),
        "records": [
            {
                "credential_ref": str(uuid.uuid4()),
                "scope": token_scope(route).to_dict(),
                "source_key": ["MULERUN_TOKEN"],
                "key_fingerprint": hmac.digest(bytes(range(32)), b"x", "sha256").hex(),
                "cli_routes": route.to_dict(),
            }
        ],
    }
    (tmp_path / "provider_credentials.json").write_text(json.dumps(payload), encoding="utf-8")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).resolve_mulerun_token(token_scope(route), payload["records"][0]["credential_ref"], TOKEN)


def test_profile_binding_rejects_empty_profile_without_path_leak(tmp_path):
    route = routes()
    source = tmp_path / "profile"
    (source / ".mulerun").mkdir(parents=True)

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError) as raised:
            with store_for(tmp_path, lease).bind_mulerun_profile(profile_scope(route), source, routes=route):
                pass

    assert str(source) not in str(raised.value)
    assert not (tmp_path / "provider_credentials.json").exists()


def test_profile_resolve_invalid_profile_is_typed_needs_credentials_without_path_leak(tmp_path):
    route = routes()
    source = make_profile(tmp_path, "profile-secret-v1")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with store.bind_mulerun_profile(profile_scope(route), source, routes=route) as bound:
            credential_ref = bound.credential_ref
        (source / ".mulerun" / "token.json").unlink()
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            with store.resolve_mulerun_profile(profile_scope(route), credential_ref, source):
                pass

    assert raised.value.kind == "invalid_profile"
    assert raised.value.state == "needs_credentials"
    assert str(source) not in str(raised.value)


def test_profile_bind_checks_store_before_copying_profile(tmp_path, monkeypatch):
    route = routes()
    source = make_profile(tmp_path)
    (tmp_path / "provider_credentials.json").write_text("null\n", encoding="utf-8")
    copied = {"count": 0}

    def fail_if_called(profile_home):
        copied["count"] += 1
        raise AssertionError("profile should not be copied before store preflight")

    monkeypatch.setattr(provider_credentials, "snapshot_mulerun_profile_to_temp_home_with_fingerprint", fail_if_called, raising=False)

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            with store_for(tmp_path, lease).bind_mulerun_profile(profile_scope(route), source, routes=route):
                pass

    assert copied["count"] == 0


def test_profile_resolve_checks_record_before_copying_profile(tmp_path, monkeypatch):
    route = routes()
    source = make_profile(tmp_path)
    copied = {"count": 0}

    def fail_if_called(profile_home):
        copied["count"] += 1
        raise AssertionError("profile should not be copied before record preflight")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            with store_for(tmp_path, lease).resolve_mulerun_profile(profile_scope(route), str(uuid.uuid4()), source):
                pass

    assert raised.value.kind == "record_missing"
    assert copied["count"] == 0


def test_profile_resolve_rechecks_record_after_snapshot_before_return(tmp_path, monkeypatch):
    route = routes()
    source = make_profile(tmp_path, "profile-secret-v1")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with store.bind_mulerun_profile(profile_scope(route), source, routes=route) as bound:
            credential_ref = bound.credential_ref
        original_snapshot = provider_credentials.snapshot_mulerun_profile_to_temp_home_with_fingerprint

        def mutate_store_during_snapshot(profile_home):
            context = original_snapshot(profile_home)
            snapshot = context.__enter__()
            stored = read_store(tmp_path)
            stored["records"][0]["key_fingerprint"] = "0" * 64
            (tmp_path / "provider_credentials.json").write_text(json.dumps(stored), encoding="utf-8")

            class Wrapped:
                def __enter__(self):
                    return snapshot

                def __exit__(self, exc_type, exc, tb):
                    return context.__exit__(exc_type, exc, tb)

            return Wrapped()

        monkeypatch.setattr(provider_credentials, "snapshot_mulerun_profile_to_temp_home_with_fingerprint", mutate_store_during_snapshot)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            with store.resolve_mulerun_profile(profile_scope(route), credential_ref, source):
                pass

    assert raised.value.kind == "fingerprint_mismatch"


def test_route_identity_allows_only_supported_sites_even_with_custom_urls():
    ok = MuleRunCliRouteIdentity(
        schema_version=1,
        login_site="cn",
        login_base_url="https://custom-login.example",
        studio_site="mulerun",
        studio_base_url="https://custom-studio.example",
    )
    assert ok.login_site == "cn"
    with pytest.raises(provider_credentials.ProviderCredentialBindingError):
        MuleRunCliRouteIdentity(
            schema_version=1,
            login_site="moon",
            login_base_url="https://custom-login.example",
            studio_site="mulerouter",
            studio_base_url="https://api.mulerouter.ai",
        )
    with pytest.raises(provider_credentials.ProviderCredentialBindingError):
        MuleRunCliRouteIdentity(
            schema_version=1,
            login_site="sg",
            login_base_url="https://api.mulerun.com",
            studio_site="moon",
            studio_base_url="https://api.mulerouter.ai",
        )



def test_profile_bind_does_not_rewrite_caller_mulerun_profile_error(tmp_path):
    route = routes()
    source = make_profile(tmp_path, "profile-secret-v1")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(MuleRunProfileError, match="caller failure"):
            with store_for(tmp_path, lease).bind_mulerun_profile(profile_scope(route), source, routes=route):
                raise MuleRunProfileError("caller failure")


def test_unsafe_profile_bind_and_resolve_do_not_leak_original_path(tmp_path):
    route = routes()
    source = make_profile(tmp_path / "safe", "profile-secret-v1")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with store.bind_mulerun_profile(profile_scope(route), source, routes=route) as bound:
            credential_ref = bound.credential_ref

    real = tmp_path / "real-profile"
    real.mkdir()
    linked = tmp_path / "leaky-linked-profile"
    linked.symlink_to(real, target_is_directory=True)
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(provider_credentials.ProviderCredentialBindingError) as bind_error:
            with store.bind_mulerun_profile(profile_scope(route), linked, routes=route):
                pass
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as resolve_error:
            with store.resolve_mulerun_profile(profile_scope(route), credential_ref, linked):
                pass

    assert str(linked) not in str(bind_error.value)
    assert str(linked) not in str(resolve_error.value)
    assert resolve_error.value.kind == "invalid_profile"
