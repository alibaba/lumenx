"""Media runtime authority freezes image/TTS scope and credential refs without I/O."""

from contextlib import ExitStack
from dataclasses import replace
from threading import RLock

import pytest

from src.audio.tts import PreparedTTS
from src.models.provider_binding import (
    MULEROUTER_HTTP_SOURCE_KEY,
    MULERUN_PROFILE_SOURCE_KEY,
    MULERUN_TOKEN_SOURCE_KEY,
    MuleRunCliRouteIdentity,
    ProviderCredentialBindingError,
    ProviderCredentialResolutionError,
    ProviderCredentialStore,
    SOURCE_KEY,
)
from src.models.provider_media_runtime import (
    MediaRuntimeAuthority,
    MediaRuntimeAuthorityError,
    bind_media_runtime_authority,
    resolve_media_runtime_authority,
    validate_media_runtime_authority,
)
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import DirectoryWriterLock, read_json
from src.utils.runtime_environment import RuntimeEnvironment

DASHSCOPE_SECRET = "sk-test-" + "d" * 44
MULEROUTER_SECRET = "mr-test-" + "m" * 44
MULERUN_TOKEN = "mrtok_" + "t" * 40
OTHER_SECRET = "sk-test-" + "e" * 44


def test_media_runtime_authority_constructor_rejects_source_key_scope_mismatch():
    credential = object()
    with pytest.raises(MediaRuntimeAuthorityError, match="source_key"):
        MediaRuntimeAuthority(
            media_kind="image",
            provider_model_id="wan2.7-image-pro",
            scope=ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing"),
            credential_ref="credential-ref",
            source_key=MULEROUTER_HTTP_SOURCE_KEY,
            credential=credential,
        )


def test_media_runtime_authority_validator_rejects_mutated_credential_identity():
    scope = ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")
    authority = MediaRuntimeAuthority(
        media_kind="image",
        provider_model_id="wan2.7-image-pro",
        scope=scope,
        credential_ref="credential-ref",
        source_key=SOURCE_KEY,
        credential=ResolvedCredential(scope, "credential-ref", DASHSCOPE_SECRET),
    )
    object.__setattr__(authority, "credential", ResolvedCredential(scope, "other-ref", DASHSCOPE_SECRET))
    with pytest.raises(MediaRuntimeAuthorityError, match="credential"):
        validate_media_runtime_authority(authority)


def runtime(values):
    return RuntimeEnvironment(values)


def store_for(root, lease):
    return ProviderCredentialStore(root, writer_lock=lease, runtime_lock=RLock())


def image_intent(model_id="wan/wan2.7-image-pro#image", mode="t2i", params=None):
    return normalize_generation_intent(
        {
            "mode": mode,
            "prompt": "paint a tiny observatory",
            "model_id": model_id,
            "params": params or {"size": "1280*1280", "seed": 0, "promptExtend": False, "watermark": False},
        },
        allowed_modes={"t2i", "i2i"},
        allowed_parameters={"size", "seed", "negative_prompt", "prompt_extend", "watermark", "quality"},
    )


def prepared_tts(model="cosyvoice-v3-flash", family="cosyvoice"):
    return PreparedTTS(
        family,
        (
            ("model", model),
            ("voice", "longxiaochun_v2"),
            ("text", "hello lumen"),
        ),
    )


def qwen_tts():
    return PreparedTTS(
        "qwen3",
        (
            ("model", "qwen3-tts-flash"),
            ("voice", "Cherry"),
            ("text", "hello lumen"),
            ("language_type", "Chinese"),
            ("stream", False),
        ),
    )


def routes(studio_base_url="https://api.mulerouter.ai"):
    return MuleRunCliRouteIdentity(
        schema_version=1,
        login_site="sg",
        login_base_url="https://api.mulerun.com",
        studio_site="mulerouter",
        studio_base_url=studio_base_url,
    )


def make_profile(root, token_text="profile-secret"):
    home = root / "profile"
    profile = home / ".mulerun"
    profile.mkdir(parents=True)
    (profile / "token.json").write_text(token_text, encoding="utf-8")
    return home


def read_store(root):
    return read_json(root / "provider_credentials.json", dict)


def test_wan_and_qwen_image_bind_dashscope_classic_scope_and_preserve_provider_model(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        wan = bind_media_runtime_authority(
            runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
            store,
            image_intent=image_intent("wan/wan2.7-image-pro#image"),
        )
        qwen = bind_media_runtime_authority(
            runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET, "DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com"}),
            store,
            image_intent=image_intent("qwen/qwen-image-2.0-pro#image"),
        )
        with wan as wan_auth, qwen as qwen_auth:
            assert wan_auth.media_kind == "image"
            assert wan_auth.provider_model_id == "wan2.7-image-pro"
            assert wan_auth.scope == ProviderScope(
                provider="dashscope",
                backend="dashscope",
                gateway="dashscope",
                base_url="https://dashscope.aliyuncs.com",
                region="cn-beijing",
            )
            assert wan_auth.source_key == SOURCE_KEY
            assert wan_auth.credential.secret == DASHSCOPE_SECRET
            assert qwen_auth.provider_model_id == "qwen-image-2.0-pro"
            assert qwen_auth.scope.base_url == "https://dashscope-intl.aliyuncs.com"
            assert qwen_auth.scope.region == "ap-southeast-1"


def test_binding_uses_one_runtime_snapshot_for_scope_and_secret(tmp_path):
    class OneShotRuntime:
        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls == 1:
                return {
                    "DASHSCOPE_API_KEY": DASHSCOPE_SECRET,
                    "DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com",
                }
            return {
                "DASHSCOPE_API_KEY": OTHER_SECRET,
                "DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com",
            }

    environment = OneShotRuntime()
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            environment,
            store,
            image_intent=image_intent("qwen/qwen-image-2.0#image"),
        ) as authority:
            assert authority.scope.base_url == "https://dashscope-intl.aliyuncs.com"
            assert authority.scope.region == "ap-southeast-1"
            assert authority.credential.secret == DASHSCOPE_SECRET
    assert environment.calls == 1


def test_workspace_image_admission_uses_existing_region_matched_classic_scope(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({
                "DASHSCOPE_API_KEY": DASHSCOPE_SECRET,
                "DASHSCOPE_WORKSPACE_ID": "image-workspace",
                "DASHSCOPE_REGION": "us-east-1",
                "DASHSCOPE_BASE_URL": "https://image-workspace.us-east-1.maas.aliyuncs.com",
            }),
            store,
            image_intent=image_intent("wan/wan2.7-image#image"),
        ) as authority:
            assert authority.scope == ProviderScope(
                provider="dashscope",
                backend="dashscope",
                gateway="dashscope",
                base_url="https://dashscope-us.aliyuncs.com",
                region="us-east-1",
            )
            assert authority.provider_model_id == "wan2.7-image"


def test_mulerouter_http_image_binding_uses_existing_store_source_key(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
            store,
            image_intent=image_intent(
                "gpt-image/gpt-image-2#image",
                params={"size": "1024x1024", "quality": "high"},
            ),
        ) as authority:
            assert authority.provider_model_id == "gpt-image-2"
            assert authority.scope == ProviderScope(
                provider="mulerouter",
                backend="mulerouter",
                gateway="mulerouter_http",
                base_url="https://api.mulerouter.ai",
            )
            assert authority.source_key == MULEROUTER_HTTP_SOURCE_KEY
            assert authority.credential.secret == MULEROUTER_SECRET
        stored = read_store(tmp_path)
        assert stored["records"][0]["source_key"] == "MULEROUTER_API_KEY"
        assert MULEROUTER_SECRET not in str(stored)


def test_mulerun_token_and_profile_image_bindings_keep_distinct_cli_authority(tmp_path):
    profile = make_profile(tmp_path / "owned-profile", "profile-secret-v1")
    route = routes()
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        intent = image_intent("gpt-image/gpt-image-2#image", params={"size": "1024x1024", "quality": "high"})
        with ExitStack() as stack:
            token_auth = stack.enter_context(bind_media_runtime_authority(
                runtime({"MULERUN_TOKEN": MULERUN_TOKEN}),
                store,
                image_intent=intent,
                mulerun_routes=route,
            ))
            profile_auth = stack.enter_context(bind_media_runtime_authority(
                runtime({"LUMENX_MULERUN_HOME": str(profile)}),
                store,
                image_intent=intent,
                mulerun_routes=route,
                mulerun_profile_home=profile,
            ))
            assert token_auth.scope.gateway == "mulerun_token"
            assert token_auth.source_key == MULERUN_TOKEN_SOURCE_KEY
            assert token_auth.credential.secret == MULERUN_TOKEN
            assert profile_auth.scope.gateway == "mulerun_profile"
            assert profile_auth.source_key == MULERUN_PROFILE_SOURCE_KEY
            assert profile_auth.credential.profile_home.exists()
            assert (profile_auth.credential.profile_home / ".mulerun" / "token.json").read_text() == "profile-secret-v1"
            (profile / ".mulerun" / "token.json").write_text("profile-secret-v2", encoding="utf-8")
            assert (profile_auth.credential.profile_home / ".mulerun" / "token.json").read_text() == "profile-secret-v1"
            assert token_auth.credential_ref != profile_auth.credential_ref
        assert not profile_auth.credential.profile_home.exists()
    serialized = str(read_store(tmp_path))
    assert "profile-secret" not in serialized
    assert str(profile) not in serialized


def test_resolve_uses_original_scope_and_ref_without_current_backend_reselection(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
            store,
            image_intent=image_intent("gpt-image/gpt-image-2#image", params={"size": "1024x1024", "quality": "high"}),
        ) as bound:
            original_scope = bound.scope
            original_ref = bound.credential_ref

        with resolve_media_runtime_authority(
            runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET, "DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
            store,
            media_kind="image",
            provider_model_id="gpt-image-2",
            scope=original_scope,
            credential_ref=original_ref,
        ) as restored:
            assert restored.scope == original_scope
            assert restored.credential_ref == original_ref
            assert restored.source_key == MULEROUTER_HTTP_SOURCE_KEY

        with pytest.raises(ProviderCredentialResolutionError) as raised:
            with resolve_media_runtime_authority(
                runtime({"MULEROUTER_API_KEY": "mr-test-" + "x" * 44}),
                store,
                media_kind="image",
                provider_model_id="gpt-image-2",
                scope=original_scope,
                credential_ref=original_ref,
            ):
                pass
        assert raised.value.kind == "fingerprint_mismatch"


def test_media_runtime_authority_validator_rejects_mutated_cli_route_identity(tmp_path):
    profile = make_profile(tmp_path / "owned-mutated-profile", "profile-secret-v1")
    route = routes()
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({"LUMENX_MULERUN_HOME": str(profile)}),
            store,
            image_intent=image_intent("gpt-image/gpt-image-2#image", params={"size": "1024x1024", "quality": "high"}),
            mulerun_routes=route,
            mulerun_profile_home=profile,
        ) as authority:
            mutated = replace(authority.credential, routes=routes("https://api.mulerun.com"))
            object.__setattr__(authority, "credential", mutated)
            with pytest.raises(MediaRuntimeAuthorityError, match="route"):
                validate_media_runtime_authority(authority)


def test_mulerouter_http_key_wins_over_token_and_profile_material_by_default(tmp_path):
    profile = make_profile(tmp_path / "owned-priority-profile", "profile-secret-v1")
    route = routes()
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({
                "MULEROUTER_API_KEY": MULEROUTER_SECRET,
                "MULERUN_TOKEN": MULERUN_TOKEN,
                "LUMENX_MULERUN_HOME": str(profile),
            }),
            store,
            image_intent=image_intent("gpt-image/gpt-image-2#image", params={"size": "1024x1024", "quality": "high"}),
            mulerun_routes=route,
            mulerun_profile_home=profile,
        ) as authority:
            assert authority.scope.gateway == "mulerouter_http"
            assert authority.source_key == MULEROUTER_HTTP_SOURCE_KEY
            assert authority.credential.secret == MULEROUTER_SECRET


def test_image_recovery_rejects_cross_provider_or_unknown_model_without_catalog_lookup(tmp_path, monkeypatch):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
            store,
            image_intent=image_intent("gpt-image/gpt-image-2#image", params={"size": "1024x1024", "quality": "high"}),
        ) as bound:
            monkeypatch.setattr(
                "src.models.provider_media_runtime.get_catalog_accessor",
                lambda: (_ for _ in ()).throw(AssertionError("recovery must not read catalog")),
            )
            with resolve_media_runtime_authority(
                runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
                store,
                media_kind="image",
                provider_model_id="gpt-image-2",
                scope=bound.scope,
                credential_ref=bound.credential_ref,
            ) as restored:
                assert restored.provider_model_id == "gpt-image-2"

            with pytest.raises(MediaRuntimeAuthorityError) as cross_provider:
                with resolve_media_runtime_authority(
                    runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
                    store,
                    media_kind="image",
                    provider_model_id="wan2.7-image-pro",
                    scope=bound.scope,
                    credential_ref=bound.credential_ref,
                ):
                    pass
            assert cross_provider.value.reason == "provider_model_scope_mismatch"

            with pytest.raises(MediaRuntimeAuthorityError) as unknown:
                with resolve_media_runtime_authority(
                    runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
                    store,
                    media_kind="image",
                    provider_model_id="new-image-model",
                    scope=bound.scope,
                    credential_ref=bound.credential_ref,
                ):
                    pass
            assert unknown.value.reason == "unsupported_provider_model"


def test_wan_and_qwen_image_recovery_do_not_read_catalog(tmp_path, monkeypatch):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        for model_id, provider_model_id in (
            ("wan/wan2.7-image#image", "wan2.7-image"),
            ("qwen/qwen-image-2.0-pro#image", "qwen-image-2.0-pro"),
        ):
            with bind_media_runtime_authority(
                runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
                store,
                image_intent=image_intent(model_id),
            ) as bound:
                monkeypatch.setattr(
                    "src.models.provider_media_runtime.get_catalog_accessor",
                    lambda: (_ for _ in ()).throw(AssertionError("recovery must not read catalog")),
                )
                with resolve_media_runtime_authority(
                    runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
                    store,
                    media_kind="image",
                    provider_model_id=provider_model_id,
                    scope=bound.scope,
                    credential_ref=bound.credential_ref,
                ) as restored:
                    assert restored.provider_model_id == provider_model_id
                monkeypatch.undo()


def test_tts_binds_endpoint_consistent_dashscope_scope_and_rejects_unknown_websocket(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
            store,
            prepared_tts=prepared_tts(),
        ) as authority:
            assert authority.media_kind == "audio"
            assert authority.provider_model_id == "cosyvoice-v3-flash"
            assert authority.scope.gateway == "dashscope"
            assert authority.scope.base_url == "https://dashscope.aliyuncs.com"
            assert authority.scope.region == "cn-beijing"
            assert authority.source_key == SOURCE_KEY

        with pytest.raises(MediaRuntimeAuthorityError) as raised:
            with bind_media_runtime_authority(
                runtime({
                    "DASHSCOPE_API_KEY": DASHSCOPE_SECRET,
                    "DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com",
                }),
                store,
                prepared_tts=prepared_tts(),
            ):
                pass
        assert raised.value.reason == "unsupported_tts_endpoint"

        with pytest.raises(MediaRuntimeAuthorityError) as custom_ws:
            with bind_media_runtime_authority(
                runtime({
                    "DASHSCOPE_API_KEY": DASHSCOPE_SECRET,
                    "DASHSCOPE_TTS_WEBSOCKET_URL": "wss://example.invalid/ws",
                }),
                store,
                prepared_tts=prepared_tts(),
            ):
                pass
        assert custom_ws.value.reason == "unsupported_tts_endpoint"


def test_qwen3_tts_uses_http_classic_scope_and_rejects_workspace_scope(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with bind_media_runtime_authority(
            runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET, "DASHSCOPE_BASE_URL": "https://dashscope-us.aliyuncs.com"}),
            store,
            prepared_tts=qwen_tts(),
        ) as authority:
            assert authority.provider_model_id == "qwen3-tts-flash"
            assert authority.scope.region == "us-east-1"

        with pytest.raises(MediaRuntimeAuthorityError) as raised:
            with bind_media_runtime_authority(
                runtime({
                    "DASHSCOPE_API_KEY": DASHSCOPE_SECRET,
                    "DASHSCOPE_WORKSPACE_ID": "tts-workspace",
                    "DASHSCOPE_REGION": "cn-beijing",
                    "DASHSCOPE_BASE_URL": "https://tts-workspace.cn-beijing.maas.aliyuncs.com",
                }),
                store,
                prepared_tts=qwen_tts(),
            ):
                pass
        assert raised.value.reason == "unsupported_tts_scope"


def test_audio_recovery_requires_prepared_tts_and_original_dashscope_scope(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        prepared = qwen_tts()
        with bind_media_runtime_authority(
            runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
            store,
            prepared_tts=prepared,
        ) as bound:
            with resolve_media_runtime_authority(
                runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET, "MULEROUTER_API_KEY": MULEROUTER_SECRET}),
                store,
                media_kind="audio",
                provider_model_id=bound.provider_model_id,
                scope=bound.scope,
                credential_ref=bound.credential_ref,
                prepared_tts=prepared,
            ) as restored:
                assert restored.scope == bound.scope
                assert restored.credential_ref == bound.credential_ref

            with pytest.raises(MediaRuntimeAuthorityError) as missing:
                with resolve_media_runtime_authority(
                    runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
                    store,
                    media_kind="audio",
                    provider_model_id=bound.provider_model_id,
                    scope=bound.scope,
                    credential_ref=bound.credential_ref,
                ):
                    pass
            assert missing.value.reason == "missing_prepared_tts"

            with pytest.raises(MediaRuntimeAuthorityError) as wrong_scope:
                with resolve_media_runtime_authority(
                    runtime({"MULEROUTER_API_KEY": MULEROUTER_SECRET}),
                    store,
                    media_kind="audio",
                    provider_model_id=bound.provider_model_id,
                    scope=ProviderScope(
                        provider="mulerouter",
                        backend="mulerouter",
                        gateway="mulerouter_http",
                        base_url="https://api.mulerouter.ai",
                    ),
                    credential_ref=bound.credential_ref,
                    prepared_tts=prepared,
                ):
                    pass
            assert wrong_scope.value.reason == "unsupported_tts_scope"


def test_rejects_unsupported_intent_or_missing_secret_before_store_write(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        video_like = replace(image_intent(), catalog_mode="video")
        with pytest.raises(MediaRuntimeAuthorityError) as bad_intent:
            with bind_media_runtime_authority(runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}), store, image_intent=video_like):
                pass
        assert bad_intent.value.reason == "unsupported_image_intent"

        with pytest.raises(ProviderCredentialBindingError):
            with bind_media_runtime_authority(runtime({}), store, image_intent=image_intent()):
                pass

    assert not (tmp_path / "provider_credentials.json").exists()


def test_exactly_one_input_kind_is_required(tmp_path):
    with DirectoryWriterLock(tmp_path) as lease:
        store = store_for(tmp_path, lease)
        with pytest.raises(MediaRuntimeAuthorityError) as none:
            with bind_media_runtime_authority(runtime({}), store):
                pass
        assert none.value.reason == "missing_media_runtime_input"
        with pytest.raises(MediaRuntimeAuthorityError) as both:
            with bind_media_runtime_authority(
                runtime({"DASHSCOPE_API_KEY": DASHSCOPE_SECRET}),
                store,
                image_intent=image_intent(),
                prepared_tts=prepared_tts(),
            ):
                pass
        assert both.value.reason == "conflicting_media_runtime_input"
