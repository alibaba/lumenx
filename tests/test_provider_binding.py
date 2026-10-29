"""Private provider credential bindings never persist or infer secrets."""

import json
import base64
import hmac
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import RLock

import pytest

from src.models import provider_binding as provider_credentials
from src.models.provider_tasks import ProviderHandle, ProviderScope, QueryCapability, ResolvedCredential
from src.utils import json_storage
from src.utils.json_storage import DirectoryWriterLock, StorageConflictError, StorageError, read_json


SECRET_A = "sk-test-" + "a" * 44
SECRET_B = "sk-test-" + "b" * 44
MULEROUTER_SECRET = "mr-test-" + "c" * 44


@pytest.fixture
def scope():
    return ProviderScope(
        provider="dashscope",
        backend="dashscope",
        gateway="dashscope",
        base_url="https://dashscope.aliyuncs.com",
        region="cn-beijing",
        workspace_id=None,
        account_identity=None,
    )



@pytest.fixture
def mulerouter_http_scope():
    return ProviderScope(
        provider="mulerouter",
        backend="mulerouter",
        gateway="mulerouter_http",
        base_url="https://api.mulerouter.ai",
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def store_for(root, lease):
    return provider_credentials.ProviderCredentialStore(root, writer_lock=lease, runtime_lock=RLock())


def handle_for(scope, credential_ref):
    return ProviderHandle(
        provider_model_id="wan2.6-i2v",
        task_id="task-original",
        scope=scope,
        credential_ref=credential_ref,
        query_capability=QueryCapability.BY_TASK_ID,
    )


def read_store(root):
    return read_json(root / "provider_credentials.json", dict)


def valid_empty_payload(schema_version=1):
    return {
        "schema_version": schema_version,
        "salt": base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("="),
        "records": [],
    }


def test_bind_creates_private_metadata_without_secret_or_public_fingerprint(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        credential = store_for(tmp_path, lease).bind(scope, SECRET_A)

    assert isinstance(credential, ResolvedCredential)
    assert credential.scope == scope
    assert credential.secret == SECRET_A
    uuid.UUID(credential.credential_ref)

    stored = read_store(tmp_path)
    assert stored["schema_version"] == 1
    assert re.fullmatch(r"[A-Za-z0-9_-]+", stored["salt"])
    assert len(stored["records"]) == 1
    record = stored["records"][0]
    assert record == {
        "credential_ref": credential.credential_ref,
        "scope": scope.to_dict(),
        "source_key": "DASHSCOPE_API_KEY",
        "key_fingerprint": record["key_fingerprint"],
    }
    assert re.fullmatch(r"[0-9a-f]{64}", record["key_fingerprint"])
    serialized = json.dumps(stored, sort_keys=True)
    assert SECRET_A not in serialized
    assert credential.secret not in repr(credential)
    assert "key_fingerprint" not in credential.to_dict()


def test_restart_same_key_and_scope_reuses_ref_without_writing_on_resolution(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        first = store_for(tmp_path, lease).bind(scope, SECRET_A)
        before = (tmp_path / "provider_credentials.json").read_bytes()

    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        second = store.bind(scope, SECRET_A)
        resolved = store.resolve_handle(handle_for(scope, first.credential_ref), SECRET_A)
        after = (tmp_path / "provider_credentials.json").read_bytes()

    assert second.credential_ref == first.credential_ref
    assert resolved == first
    assert after == before


def test_changed_key_gets_new_ref_and_old_handle_needs_original_credentials(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        changed = store.bind(scope, SECRET_B)

        assert changed.credential_ref != original.credential_ref
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_handle(handle_for(scope, original.credential_ref), SECRET_B)
        assert raised.value.state == "needs_credentials"
        assert raised.value.kind == "fingerprint_mismatch"

        restored = store.resolve_handle(handle_for(scope, original.credential_ref), SECRET_A)

    assert restored == original
    assert len(read_store(tmp_path)["records"]) == 2


def test_typed_needs_credentials_does_not_latch_store_failure(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError):
            store.resolve_handle(handle_for(scope, original.credential_ref), SECRET_B)
        changed = store.bind(scope, SECRET_B)

    assert changed.credential_ref != original.credential_ref


def test_same_key_different_scope_gets_new_ref_and_cannot_borrow_old_handle(tmp_path, scope):
    other_scope = replace(scope, base_url="https://dashscope-intl.aliyuncs.com", region="ap-southeast-1")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        changed_scope = store.bind(other_scope, SECRET_A)

        assert changed_scope.credential_ref != original.credential_ref
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_handle(handle_for(other_scope, original.credential_ref), SECRET_A)

    assert raised.value.state == "needs_credentials"
    assert raised.value.kind == "scope_mismatch"


def test_same_key_classic_and_workspace_scopes_get_separate_verified_bindings(tmp_path, scope):
    workspace_scope = replace(
        scope,
        gateway="dashscope_workspace",
        base_url="https://wan-workspace.cn-beijing.maas.aliyuncs.com",
        workspace_id="wan-workspace",
    )
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        classic = store.bind(scope, SECRET_A)
        workspace = store.bind(workspace_scope, SECRET_A)
        resolved_workspace = store.resolve_handle(
            handle_for(workspace_scope, workspace.credential_ref),
            SECRET_A,
        )

    assert workspace.credential_ref != classic.credential_ref
    assert resolved_workspace == workspace
    stored = read_store(tmp_path)
    assert len(stored["records"]) == 2
    assert SECRET_A not in json.dumps(stored, sort_keys=True)


@pytest.mark.parametrize("bad_scope", [
    lambda s: replace(s, provider="aliyun"),
    lambda s: replace(s, backend="wanx"),
    lambda s: replace(
        s,
        gateway="dashscope_workspace",
        base_url="https://trial.cn-beijing.maas.aliyuncs.com",
        workspace_id="trial",
    ),
    lambda s: replace(s, account_identity="acct-1"),
])
def test_only_valid_dashscope_scope_without_account_identity_is_bound(
    tmp_path, scope, bad_scope,
):
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError):
            store_for(tmp_path, lease).bind(bad_scope(scope), SECRET_A)
    assert not (tmp_path / "provider_credentials.json").exists()


def test_explicit_source_key_is_not_generalized(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError):
            store_for(tmp_path, lease).bind(scope, SECRET_A, source_key="OTHER_API_KEY")
    assert not (tmp_path / "provider_credentials.json").exists()


def test_mulerouter_http_binding_is_private_reusable_and_exact_source_key(
    tmp_path, mulerouter_http_scope,
):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        credential = store.bind(
            mulerouter_http_scope,
            MULEROUTER_SECRET,
            source_key="MULEROUTER_API_KEY",
        )
        reused = store.bind(
            mulerouter_http_scope,
            MULEROUTER_SECRET,
            source_key="MULEROUTER_API_KEY",
        )
        resolved = store.resolve_binding(
            mulerouter_http_scope,
            credential.credential_ref,
            MULEROUTER_SECRET,
            source_key="MULEROUTER_API_KEY",
        )

    assert reused.credential_ref == credential.credential_ref
    assert resolved == credential
    stored = read_store(tmp_path)
    assert stored["records"] == [
        {
            "credential_ref": credential.credential_ref,
            "scope": mulerouter_http_scope.to_dict(),
            "source_key": "MULEROUTER_API_KEY",
            "key_fingerprint": stored["records"][0]["key_fingerprint"],
        }
    ]
    serialized = json.dumps(stored, sort_keys=True)
    assert MULEROUTER_SECRET not in serialized
    assert "MULEROUTER_API_KEY" in serialized


def test_mulerouter_http_handle_resolution_requires_original_source_and_secret(
    tmp_path, mulerouter_http_scope,
):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        credential = store.bind(
            mulerouter_http_scope,
            MULEROUTER_SECRET,
            source_key="MULEROUTER_API_KEY",
        )
        handle = handle_for(mulerouter_http_scope, credential.credential_ref)
        restored_from_handle = store.resolve_handle(handle, MULEROUTER_SECRET)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as wrong_secret:
            store.resolve_binding(
                mulerouter_http_scope,
                credential.credential_ref,
                SECRET_A,
                source_key="MULEROUTER_API_KEY",
            )
        restored = store.resolve_binding(
            mulerouter_http_scope,
            credential.credential_ref,
            MULEROUTER_SECRET,
            source_key="MULEROUTER_API_KEY",
        )

    assert wrong_secret.value.kind == "fingerprint_mismatch"
    assert restored_from_handle == credential
    assert restored == credential


def test_dashscope_and_mulerouter_http_bindings_coexist_without_source_borrowing(
    tmp_path, scope, mulerouter_http_scope,
):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        dashscope = store.bind(scope, SECRET_A)
        mulerouter = store.bind(
            mulerouter_http_scope,
            MULEROUTER_SECRET,
            source_key="MULEROUTER_API_KEY",
        )
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_binding(
                scope,
                mulerouter.credential_ref,
                MULEROUTER_SECRET,
            )

    assert mulerouter.credential_ref != dashscope.credential_ref
    assert raised.value.kind == "scope_mismatch"
    stored = read_store(tmp_path)
    assert {record["source_key"] for record in stored["records"]} == {
        "DASHSCOPE_API_KEY",
        "MULEROUTER_API_KEY",
    }


@pytest.mark.parametrize(
    ("source_key", "scope_fixture"),
    [
        ("MULEROUTER_API_KEY", "scope"),
        ("DASHSCOPE_API_KEY", "mulerouter_http_scope"),
    ],
)
def test_scope_and_source_key_must_match_before_write(
    tmp_path, request, source_key, scope_fixture,
):
    selected_scope = request.getfixturevalue(scope_fixture)
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError):
            store_for(tmp_path, lease).bind(selected_scope, SECRET_A, source_key=source_key)
    assert not (tmp_path / "provider_credentials.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: replace(s, region="cn-beijing"),
        lambda s: replace(s, workspace_id="workspace"),
        lambda s: replace(s, account_identity="account"),
        lambda s: replace(s, base_url="https://user:password@api.mulerouter.ai"),
        lambda s: replace(s, base_url="https://api.mulerouter.ai?token=secret"),
        lambda s: replace(s, base_url="https://api.mulerouter.ai/#fragment"),
        lambda s: replace(s, base_url="https://api.mulerun.com"),
    ],
)
def test_mulerouter_http_scope_is_official_endpoint_only(
    tmp_path, mulerouter_http_scope, mutation,
):
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(provider_credentials.ProviderCredentialBindingError):
            store_for(tmp_path, lease).bind(
                mutation(mulerouter_http_scope),
                MULEROUTER_SECRET,
                source_key="MULEROUTER_API_KEY",
            )
    assert not (tmp_path / "provider_credentials.json").exists()


def test_store_parser_rejects_source_key_that_does_not_match_stored_scope(
    tmp_path, mulerouter_http_scope,
):
    ref = str(uuid.uuid4())
    salt = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    fingerprint = hmac.digest(bytes(range(32)), MULEROUTER_SECRET.encode("ascii"), "sha256").hex()
    payload = {
        "schema_version": 1,
        "salt": salt,
        "records": [
            {
                "credential_ref": ref,
                "scope": mulerouter_http_scope.to_dict(),
                "source_key": "DASHSCOPE_API_KEY",
                "key_fingerprint": fingerprint,
            },
        ],
    }
    (tmp_path / "provider_credentials.json").write_text(json.dumps(payload), encoding="utf-8")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).resolve_binding(
                mulerouter_http_scope,
                ref,
                MULEROUTER_SECRET,
                source_key="MULEROUTER_API_KEY",
            )


@pytest.mark.parametrize("content", [
    "{}",
    '{"schema_version":1,"salt":"short","records":[]}',
    '{"schema_version":1,"salt":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","records":[],"extra":true}',
    '{"schema_version":1,"salt":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","records":[{"credential_ref":"not-uuid","scope":{},"source_key":"DASHSCOPE_API_KEY","key_fingerprint":"' + "0" * 64 + '"}]}',
    '{"schema_version":1,"salt":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","records":[{"credential_ref":"' + str(uuid.uuid4()) + '","scope":{},"source_key":"DASHSCOPE_API_KEY","key_fingerprint":"nothex"}]}',
    '{"schema_version":1,"salt":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","records":[],"records":[]}',
])
def test_corrupt_or_non_canonical_store_rejects_before_returning_credentials(
    tmp_path, scope, content,
):
    path = tmp_path / "provider_credentials.json"
    path.write_text(content, encoding="utf-8")
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).bind(scope, SECRET_A)


def test_read_corruption_latches_failed_store_until_instance_is_rebuilt(tmp_path, scope):
    path = tmp_path / "provider_credentials.json"
    path.write_text("null\n", encoding="utf-8")
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(StorageError):
            store.bind(scope, SECRET_A)
        path.unlink()
        with pytest.raises(StorageError, match="failed"):
            store.bind(scope, SECRET_A)
        recovered = store_for(tmp_path, lease).bind(scope, SECRET_A)

    assert recovered.scope == scope


def test_json_null_store_is_corrupt_not_missing_or_repaired(tmp_path, scope):
    path = tmp_path / "provider_credentials.json"
    path.write_text("null\n", encoding="utf-8")
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).bind(scope, SECRET_A)
    assert path.read_text(encoding="utf-8") == "null\n"


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_requires_exact_integer_not_bool_or_float(
    tmp_path, scope, schema_version,
):
    path = tmp_path / "provider_credentials.json"
    path.write_text(json.dumps(valid_empty_payload(schema_version)), encoding="utf-8")
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).bind(scope, SECRET_A)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == schema_version


def test_duplicate_credential_ref_rejects_before_resolution(tmp_path, scope):
    ref = str(uuid.uuid4())
    salt = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    fingerprint = hmac.digest(bytes(range(32)), SECRET_A.encode("ascii"), "sha256").hex()
    payload = {
        "schema_version": 1,
        "salt": salt,
        "records": [
            {
                "credential_ref": ref,
                "scope": scope.to_dict(),
                "source_key": "DASHSCOPE_API_KEY",
                "key_fingerprint": fingerprint,
            },
            {
                "credential_ref": ref,
                "scope": scope.to_dict(),
                "source_key": "DASHSCOPE_API_KEY",
                "key_fingerprint": fingerprint,
            },
        ],
    }
    (tmp_path / "provider_credentials.json").write_text(json.dumps(payload), encoding="utf-8")

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).resolve_handle(handle_for(scope, ref), SECRET_A)


def test_symlink_store_path_is_rejected_without_touching_target(tmp_path, scope):
    target = tmp_path / "target.json"
    target.write_text('{"external":true}', encoding="utf-8")
    (tmp_path / "provider_credentials.json").symlink_to(target)

    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(StorageError):
            store_for(tmp_path, lease).bind(scope, SECRET_A)

    assert target.read_text(encoding="utf-8") == '{"external":true}'


def test_symlink_failure_latches_failed_store_until_instance_is_rebuilt(tmp_path, scope):
    target = tmp_path / "target.json"
    target.write_text('{"external":true}', encoding="utf-8")
    path = tmp_path / "provider_credentials.json"
    path.symlink_to(target)

    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(StorageError):
            store.bind(scope, SECRET_A)
        path.unlink()
        with pytest.raises(StorageError, match="failed"):
            store.bind(scope, SECRET_A)
        recovered = store_for(tmp_path, lease).bind(scope, SECRET_A)

    assert recovered.scope == scope
    assert target.read_text(encoding="utf-8") == '{"external":true}'


def test_save_failure_returns_no_credential_and_keeps_store_failed(tmp_path, scope, monkeypatch):
    real_write = provider_credentials.atomic_write_json
    calls = {"count": 0}

    def fail_once(path, payload):
        calls["count"] += 1
        if calls["count"] == 1:
            raise StorageError("disk full")
        real_write(path, payload)

    monkeypatch.setattr(provider_credentials, "atomic_write_json", fail_once)
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(StorageError, match="disk full"):
            store.bind(scope, SECRET_A)
        with pytest.raises(StorageError, match="failed"):
            store.bind(scope, SECRET_A)

    assert not (tmp_path / "provider_credentials.json").exists()


def test_requires_matching_active_writer_lease(tmp_path, scope):
    owner = DirectoryWriterLock(tmp_path / "owner").acquire()
    try:
        with pytest.raises(StorageConflictError):
            store_for(tmp_path / "other", owner).bind(scope, SECRET_A)
        owner.close()
        with pytest.raises(StorageConflictError):
            store_for(tmp_path / "owner", owner).bind(scope, SECRET_A)
    finally:
        owner.close()


def test_constructor_requires_caller_runtime_lock(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        with pytest.raises(TypeError):
            provider_credentials.ProviderCredentialStore(tmp_path, writer_lock=lease)
        with pytest.raises(StorageConflictError):
            provider_credentials.ProviderCredentialStore(
                tmp_path, writer_lock=lease, runtime_lock=object(),
            )


def test_concurrent_same_binding_preserves_one_record(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with ThreadPoolExecutor(max_workers=6) as executor:
            refs = list(executor.map(lambda _: store.bind(scope, SECRET_A).credential_ref, range(24)))

    assert len(set(refs)) == 1
    assert len(read_store(tmp_path)["records"]) == 1


def test_resolution_errors_do_not_echo_secret_or_metadata(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        fingerprint = read_store(tmp_path)["records"][0]["key_fingerprint"]
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_handle(handle_for(scope, original.credential_ref), SECRET_B)

    message = str(raised.value)
    assert SECRET_A not in message
    assert SECRET_B not in message
    assert fingerprint not in message
    assert raised.value.state == "needs_credentials"


@pytest.mark.parametrize("bad_secret", ["", "has space", "含中文", None])
def test_invalid_resolution_secret_is_typed_needs_credentials_without_echo(
    tmp_path, scope, bad_secret,
):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_handle(handle_for(scope, original.credential_ref), bad_secret)

    assert raised.value.state == "needs_credentials"
    assert raised.value.kind == "invalid_secret"
    if bad_secret:
        assert str(bad_secret) not in str(raised.value)


def test_incomplete_handle_is_typed_needs_credentials_without_store_write(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_handle(replace(handle_for(scope, str(uuid.uuid4())), scope=None), SECRET_A)

    assert raised.value.state == "needs_credentials"
    assert raised.value.kind == "missing_handle_binding"
    assert not (tmp_path / "provider_credentials.json").exists()


def test_uncertain_commit_blocks_reuse_until_store_is_recreated(tmp_path, scope, monkeypatch):
    real_write = json_storage.atomic_write_json

    def uncertain(path, payload):
        real_write(path, payload)
        raise StorageError("sync uncertain", commit_uncertain=True)

    monkeypatch.setattr(provider_credentials, "atomic_write_json", uncertain)
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(StorageError) as raised:
            store.bind(scope, SECRET_A)
        assert raised.value.commit_uncertain
        with pytest.raises(StorageError, match="failed"):
            store.resolve_handle(handle_for(scope, str(uuid.uuid4())), SECRET_A)

    monkeypatch.setattr(provider_credentials, "atomic_write_json", real_write)
    with DirectoryWriterLock(tmp_path) as lease:
        recovered = store_for(tmp_path, lease).bind(scope, SECRET_A)

    assert recovered.scope == scope


def test_two_store_instances_share_runtime_lock_and_preserve_distinct_scope_records(
    tmp_path, scope,
):
    other_scope = replace(scope, base_url="https://dashscope-us.aliyuncs.com", region="us-east-1")
    runtime_lock = RLock()
    with DirectoryWriterLock(tmp_path) as lease:
        first = provider_credentials.ProviderCredentialStore(
            tmp_path, writer_lock=lease, runtime_lock=runtime_lock,
        )
        second = provider_credentials.ProviderCredentialStore(
            tmp_path, writer_lock=lease, runtime_lock=runtime_lock,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            refs = list(executor.map(
                lambda item: item[0].bind(item[1], SECRET_A).credential_ref,
                [(first, scope), (second, other_scope)],
            ))

    assert len(set(refs)) == 2
    stored = read_store(tmp_path)
    assert len(stored["records"]) == 2
    assert {record["scope"]["base_url"] for record in stored["records"]} == {
        scope.base_url,
        other_scope.base_url,
    }


def test_resolve_binding_reads_existing_ref_without_synthetic_handle_or_writing(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        before = (tmp_path / "provider_credentials.json").read_bytes()
        resolved = store.resolve_binding(scope, original.credential_ref, SECRET_A)
        after = (tmp_path / "provider_credentials.json").read_bytes()

    assert resolved == original
    assert after == before


def test_resolve_binding_changed_key_does_not_create_new_record(tmp_path, scope):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        original = store.bind(scope, SECRET_A)
        before = read_store(tmp_path)
        with pytest.raises(provider_credentials.ProviderCredentialResolutionError) as raised:
            store.resolve_binding(scope, original.credential_ref, SECRET_B)
        after = read_store(tmp_path)

    assert raised.value.kind == "fingerprint_mismatch"
    assert after == before
    assert len(after["records"]) == 1
