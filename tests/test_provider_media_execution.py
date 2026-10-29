from __future__ import annotations

from dataclasses import asdict
import base64
import hashlib
import math
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.audio.tts import PreparedTTS
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_media_intents import build_accepted_sync_artifact_record, prepared_tts_intent_payload
from src.models.provider_media_runtime import MediaRuntimeAuthority
from src.models.provider_tasks import ProviderScope, ResolvedCredential
from src.models.image import WanxImageModel
from src.models.mulerouter import MuleRouterImageModel
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import DirectoryWriterLock
from src.utils.media_versions import freeze_media
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan3_image_validation import JPEG_240_BASE64


DASHSCOPE_SECRET = "sk-test-" + "d" * 44
MULEROUTER_SECRET = "mr-test-" + "m" * 44


def dashscope_scope() -> ProviderScope:
    return ProviderScope("dashscope", "dashscope", "dashscope", "https://dashscope.aliyuncs.com", "cn-beijing")


def mulerun_scope() -> ProviderScope:
    return ProviderScope("mulerouter", "mulerun_cli", "mulerun_token", "https://api.mulerouter.ai")


def dashscope_authority(*, media_kind="image", model="wan2.7-image-pro") -> MediaRuntimeAuthority:
    scope = dashscope_scope()
    return MediaRuntimeAuthority(
        media_kind=media_kind,
        provider_model_id=model,
        scope=scope,
        credential_ref="cred-dashscope",
        source_key="DASHSCOPE_API_KEY",
        credential=ResolvedCredential(scope, "cred-dashscope", DASHSCOPE_SECRET),
    )


def image_intent(*, mode="t2i", params=None) -> GenerationIntent:
    return GenerationIntent(
        model_id="wan/wan2.7-image-pro#image",
        catalog_mode="image",
        mode=mode,
        prompt="paint a glass moon",
        params=params if params is not None else {"size": "1280*1280", "seed": 0, "prompt_extend": False, "watermark": False},
        sources={"model_id": "request", "mode": "request", "prompt": "request"},
        request_fields=("mode", "model_id", "prompt", "params.size", "params.seed", "params.prompt_extend", "params.watermark"),
    )


def submitting(record: ProviderExecutionRecord) -> ProviderExecutionRecord:
    return ProviderExecutionRecord(**{**record.model_dump(mode="json"), "phase": "submitting"})


def image_record(*, intent=None, media=(), provider_model_id="wan2.7-image-pro", scope=None, credential_ref="cred-dashscope"):
    return submitting(build_accepted_sync_artifact_record(
        intent=intent or image_intent(),
        provider_model_id=provider_model_id,
        media_kind="image",
        media=media,
        scope=scope or dashscope_scope(),
        credential_ref=credential_ref,
        attempt_id="attempt-image",
        output_ref="assets/atelier/candidate-attempt.png",
    ))


def accepted_image_record(**kwargs) -> ProviderExecutionRecord:
    accepted = image_record(**kwargs)
    return ProviderExecutionRecord(**{**accepted.model_dump(mode="json"), "phase": "accepted"})


def prepared_tts() -> PreparedTTS:
    return PreparedTTS(
        "cosyvoice",
        (
            ("model", "cosyvoice-v3-flash"),
            ("voice", "longanyang"),
            ("text", "hello lumen"),
            ("volume", 0),
        ),
    )


def audio_record(prepared: PreparedTTS | None = None) -> ProviderExecutionRecord:
    prepared = prepared or prepared_tts()
    return submitting(build_accepted_sync_artifact_record(
        intent=prepared_tts_intent_payload(prepared, request_fields=("text", "voice", "params.volume")),
        provider_model_id=prepared.model,
        media_kind="audio",
        media=(),
        scope=dashscope_scope(),
        credential_ref="cred-dashscope",
        attempt_id="attempt-audio",
        output_ref="audio/atelier/candidate-attempt.mp3",
    ))


@pytest.fixture
def media_root(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    lease = DirectoryWriterLock(root).acquire()
    try:
        yield SimpleNamespace(root=root, lease=lease)
    finally:
        lease.close()


def write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(JPEG_240_BASE64))


def write_wav(path: Path) -> None:
    write_wav_frames(path, 800)


def write_wav_frames(path: Path, frames: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * frames)


class FakeWanx:
    calls: list[dict] = []

    def __init__(self, config, *, env=None):
        self.config = config
        self.env = dict(env or {})

    def generate(self, prompt, output_path, **kwargs):
        self.calls.append({"prompt": prompt, "output_path": output_path, "kwargs": kwargs, "env": self.env, "config": self.config})
        write_jpeg(Path(output_path))
        return output_path, 1.25


class FakeTTS:
    calls: list[dict] = []

    def __init__(self, *, api_key=None, model=None, voice=None, env=None):
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.env = dict(env or {})

    def execute(self, prepared, output_path):
        self.calls.append({"prepared": prepared, "output_path": output_path, "api_key": self.api_key, "model": self.model, "voice": self.voice, "env": self.env})
        write_wav(Path(output_path))
        return output_path, 0.5, "request-id"




def test_bounds_reject_nan_and_infinite_float_values():
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError

    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ProviderMediaExecutionError, match="audio_max_duration_seconds"):
            SyncArtifactBounds(1024, 1024, audio_max_duration_seconds=value)
        with pytest.raises(ProviderMediaExecutionError, match="audio_duration_tolerance_seconds"):
            SyncArtifactBounds(1024, 1024, audio_duration_tolerance_seconds=value)


def test_execution_requires_durable_submitting_record_before_provider_io(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    def fail_factory(*_args, **_kwargs):
        raise AssertionError("accepted records must fail before provider construction")

    with pytest.raises(Exception, match="submitting"):
        execute_sync_artifact_to_private_file(
            accepted_image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=fail_factory,
        )


def test_dashscope_image_execution_uses_bound_scope_and_normalizes_jpeg_to_private_png(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    FakeWanx.calls.clear()
    result = execute_sync_artifact_to_private_file(
        image_record(),
        dashscope_authority(),
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        image_model_factory=FakeWanx,
    )

    assert result.media_kind == "image"
    assert result.encoding == "image/png"
    assert result.path.suffix == ".png"
    assert result.path.is_file()
    assert result.sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert result.size_bytes == result.path.stat().st_size
    assert result.path.read_bytes().startswith(b"\x89PNG")
    call = FakeWanx.calls[0]
    assert call["prompt"] == "paint a glass moon"
    assert call["kwargs"]["model_name"] == "wan2.7-image-pro"
    assert call["kwargs"]["size"] == "1280*1280"
    assert call["kwargs"]["seed"] == 0
    assert call["kwargs"]["prompt_extend"] is False
    assert call["kwargs"]["watermark"] is False
    assert call["env"] == {"DASHSCOPE_API_KEY": DASHSCOPE_SECRET, "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com"}
    assert not (media_root.root / "assets" / "atelier" / "candidate-attempt.png").exists()


def test_i2i_execution_reads_frozen_reference_blob_not_mutable_source(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    (media_root.root / "uploads").mkdir()
    source = media_root.root / "uploads" / "ref.png"
    original = rgb_png_bytes("original-ref")
    source.write_bytes(original)
    version = freeze_media(
        "uploads/ref.png",
        output_root=media_root.root,
        writer_lock=media_root.lease,
        role="reference_image",
        ordinal=0,
        max_bytes=1024 * 1024,
    )
    source.write_bytes(rgb_png_bytes("mutated-ref"))
    seen_ref_bytes = []

    class InspectingWanx(FakeWanx):
        def generate(self, prompt, output_path, **kwargs):
            ref_paths = kwargs["ref_image_paths"]
            assert len(ref_paths) == 1
            ref_path = Path(ref_paths[0])
            assert ref_path.is_file()
            assert media_root.root not in ref_path.parents
            seen_ref_bytes.append(ref_path.read_bytes())
            return super().generate(prompt, output_path, **kwargs)

    result = execute_sync_artifact_to_private_file(
        image_record(intent=image_intent(mode="i2i"), media=[asdict(version)]),
        dashscope_authority(),
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        image_model_factory=InspectingWanx,
    )

    assert result.path.read_bytes().startswith(b"\x89PNG")
    assert seen_ref_bytes == [original]



def test_dashscope_i2i_private_reference_is_consumed_by_actual_resolver(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    (media_root.root / "uploads").mkdir()
    source = media_root.root / "uploads" / "ref.png"
    source.write_bytes(rgb_png_bytes("dashscope-actual-resolver-ref"))
    version = freeze_media(
        "uploads/ref.png",
        output_root=media_root.root,
        writer_lock=media_root.lease,
        role="reference_image",
        ordinal=0,
        max_bytes=1024 * 1024,
    )
    resolved_inputs = []

    class ResolvingWanx(WanxImageModel):
        def generate(self, prompt, output_path, **kwargs):
            ref_paths = kwargs.get("ref_image_paths") or []
            assert ref_paths
            for ref in ref_paths:
                resolved_inputs.append(self._resolve_wan26_reference_image(ref, model_name=kwargs["model_name"]))
            write_jpeg(Path(output_path))
            return output_path, 0.1

    result = execute_sync_artifact_to_private_file(
        image_record(intent=image_intent(mode="i2i"), media=[asdict(version)]),
        dashscope_authority(),
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        image_model_factory=ResolvingWanx,
    )

    assert result.path.read_bytes().startswith(b"\x89PNG")
    assert len(resolved_inputs) == 1
    assert resolved_inputs[0].startswith("data:image/png;base64,")


def test_mulerouter_i2i_private_reference_is_consumed_by_actual_resolver(tmp_path, media_root):
    from src.models.provider_binding import MULEROUTER_HTTP_SOURCE_KEY
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    (media_root.root / "uploads").mkdir()
    source = media_root.root / "uploads" / "ref.png"
    source.write_bytes(rgb_png_bytes("mule-actual-resolver-ref"))
    version = freeze_media(
        "uploads/ref.png",
        output_root=media_root.root,
        writer_lock=media_root.lease,
        role="reference_image",
        ordinal=0,
        max_bytes=1024 * 1024,
    )
    scope = ProviderScope("mulerouter", "mulerouter", "mulerouter_http", "https://api.mulerouter.ai")
    authority = MediaRuntimeAuthority(
        media_kind="image",
        provider_model_id="gpt-image-2",
        scope=scope,
        credential_ref="cred-http",
        source_key=MULEROUTER_HTTP_SOURCE_KEY,
        credential=ResolvedCredential(scope, "cred-http", MULEROUTER_SECRET),
    )
    resolved_inputs = []

    class ResolvingMule(MuleRouterImageModel):
        def generate_with_bound_http(self, prompt, output_path, credential, **kwargs):
            ref_paths = kwargs.get("ref_image_paths") or []
            assert ref_paths
            for ref in ref_paths:
                resolved_inputs.append(self._resolve_image_input(ref))
            write_jpeg(Path(output_path))
            return output_path, 0.1

    result = execute_sync_artifact_to_private_file(
        image_record(
            intent=GenerationIntent(
                model_id="openai/gpt-image-2#image",
                catalog_mode="image",
                mode="i2i",
                prompt="edit",
                params={"size": "1024x1024", "quality": "high"},
                sources={"model_id": "request", "mode": "request", "prompt": "request"},
                request_fields=("mode", "model_id", "prompt", "params.size", "params.quality"),
            ),
            media=[asdict(version)],
            provider_model_id="gpt-image-2",
            scope=scope,
            credential_ref="cred-http",
        ),
        authority,
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        image_model_factory=ResolvingMule,
    )

    assert result.path.read_bytes().startswith(b"\x89PNG")
    assert len(resolved_inputs) == 1
    assert resolved_inputs[0].startswith("data:image/png;base64,")


def test_authority_mismatch_fails_before_provider_io(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    def fail_factory(*_args, **_kwargs):
        raise AssertionError("provider must not be constructed on authority mismatch")

    with pytest.raises(ValueError, match="authority"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(model="wan2.7-image"),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=fail_factory,
        )


def test_mulerun_cli_rejects_unsupported_seed_before_provider_io(tmp_path, media_root):
    from src.models.provider_binding import MuleRunCliRouteIdentity, ResolvedMuleRunTokenCredential
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    route = MuleRunCliRouteIdentity(1, "sg", "https://login.example", "mulerouter", "https://api.mulerouter.ai")
    scope = mulerun_scope()
    authority = MediaRuntimeAuthority(
        media_kind="image",
        provider_model_id="gpt-image-2",
        scope=scope,
        credential_ref="cred-cli",
        source_key="MULERUN_TOKEN",
        credential=ResolvedMuleRunTokenCredential(scope, "cred-cli", route, "mrtok_" + "t" * 40),
    )

    def fail_factory(*_args, **_kwargs):
        raise AssertionError("unsupported CLI params must fail before provider construction")

    with pytest.raises(ValueError, match="unsupported"):
        execute_sync_artifact_to_private_file(
            image_record(
                intent=GenerationIntent(
                    model_id="openai/gpt-image-2#image",
                    catalog_mode="image",
                    mode="t2i",
                    prompt="draw",
                    params={"size": "1024x1024", "seed": 7},
                    sources={"model_id": "request", "mode": "request", "prompt": "request"},
                    request_fields=("mode", "model_id", "prompt", "params.size", "params.seed"),
                ),
                provider_model_id="gpt-image-2",
                scope=scope,
                credential_ref="cred-cli",
            ),
            authority,
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=fail_factory,
        )



def test_mulerun_cli_quality_is_preserved_in_bound_cli_argv(tmp_path, media_root, monkeypatch):
    from src.models import mulerouter
    from src.models.provider_binding import MuleRunCliRouteIdentity, ResolvedMuleRunTokenCredential
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    route = MuleRunCliRouteIdentity(1, "sg", "https://login.example", "mulerouter", "https://api.mulerouter.ai")
    scope = mulerun_scope()
    authority = MediaRuntimeAuthority(
        media_kind="image",
        provider_model_id="gpt-image-2",
        scope=scope,
        credential_ref="cred-cli",
        source_key="MULERUN_TOKEN",
        credential=ResolvedMuleRunTokenCredential(scope, "cred-cli", route, "mrtok_" + "t" * 40),
    )
    calls = []

    real_run = mulerouter.subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "mulerun":
            calls.append((cmd, kwargs))
            return SimpleNamespace(returncode=0, stdout='{\"images\":[{\"url\":\"https://files.example/result.jpg\"}]}', stderr='')
        return real_run(cmd, **kwargs)

    def fake_download(url, output_path):
        write_jpeg(Path(output_path))
        return output_path

    monkeypatch.setattr(mulerouter.subprocess, "run", fake_run)
    monkeypatch.setattr(mulerouter, "_download_file", fake_download)

    result = execute_sync_artifact_to_private_file(
        image_record(
            intent=GenerationIntent(
                model_id="openai/gpt-image-2#image",
                catalog_mode="image",
                mode="t2i",
                prompt="draw",
                params={"size": "1024x1024", "quality": "high"},
                sources={"model_id": "request", "mode": "request", "prompt": "request"},
                request_fields=("mode", "model_id", "prompt", "params.size", "params.quality"),
            ),
            provider_model_id="gpt-image-2",
            scope=scope,
            credential_ref="cred-cli",
        ),
        authority,
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
    )

    cmd = calls[0][0]
    assert result.path.read_bytes().startswith(b"\x89PNG")
    assert cmd[cmd.index("--quality") + 1] == "high"
    assert cmd[cmd.index("--size") + 1] == "1024x1024"


def test_mulerouter_http_execution_uses_bound_http_credential_and_max_bytes_alias(tmp_path, media_root):
    from src.models.provider_binding import MULEROUTER_HTTP_SOURCE_KEY
    from src.models.provider_media_execution import execute_sync_artifact_to_private_file

    scope = ProviderScope("mulerouter", "mulerouter", "mulerouter_http", "https://api.mulerouter.ai")
    authority = MediaRuntimeAuthority(
        media_kind="image",
        provider_model_id="gpt-image-2",
        scope=scope,
        credential_ref="cred-http",
        source_key=MULEROUTER_HTTP_SOURCE_KEY,
        credential=ResolvedCredential(scope, "cred-http", MULEROUTER_SECRET),
    )
    calls = []

    class FakeMuleHttp:
        def __init__(self, config, *, env=None):
            self.config = config
            self.env = dict(env or {})

        def generate_with_bound_http(self, prompt, output_path, credential, **kwargs):
            calls.append({"prompt": prompt, "credential": credential, "kwargs": kwargs, "env": self.env})
            write_jpeg(Path(output_path))
            return output_path, 0.25

        def generate(self, *_args, **_kwargs):
            raise AssertionError("dynamic MuleRouter generate path must not run")

    result = execute_sync_artifact_to_private_file(
        image_record(
            intent=GenerationIntent(
                model_id="openai/gpt-image-2#image",
                catalog_mode="image",
                mode="t2i",
                prompt="draw",
                params={"size": "1024x1024", "quality": "high"},
                sources={"model_id": "request", "mode": "request", "prompt": "request"},
                request_fields=("mode", "model_id", "prompt", "params.size", "params.quality"),
            ),
            provider_model_id="gpt-image-2",
            scope=scope,
            credential_ref="cred-http",
        ),
        authority,
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        max_bytes=1024 * 1024,
        image_model_factory=FakeMuleHttp,
    )

    assert result.path.read_bytes().startswith(b"\x89PNG")
    assert calls[0]["credential"].secret == MULEROUTER_SECRET
    assert calls[0]["credential"].scope == scope
    assert calls[0]["kwargs"] == {"size": "1024x1024", "quality": "high"}
    assert calls[0]["env"] == {}


def test_tts_execution_uses_prepared_record_and_normalizes_wav_to_mp3(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    FakeTTS.calls.clear()
    prepared = prepared_tts()
    result = execute_sync_artifact_to_private_file(
        audio_record(prepared),
        dashscope_authority(media_kind="audio", model="cosyvoice-v3-flash"),
        output_root=media_root.root,
        scratch_dir=tmp_path / "scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        tts_processor_factory=FakeTTS,
    )

    assert result.media_kind == "audio"
    assert result.encoding == "audio/mpeg"
    assert result.path.suffix == ".mp3"
    assert result.path.is_file()
    assert result.sha256 == hashlib.sha256(result.path.read_bytes()).hexdigest()
    assert result.size_bytes == result.path.stat().st_size
    assert FakeTTS.calls[0]["prepared"] == prepared
    assert FakeTTS.calls[0]["api_key"] == DASHSCOPE_SECRET
    assert FakeTTS.calls[0]["env"] == {"DASHSCOPE_API_KEY": DASHSCOPE_SECRET, "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com"}



def test_rejects_symlink_or_oversized_provider_image_before_transcode(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError, execute_sync_artifact_to_private_file

    outside = tmp_path / "outside.jpg"
    write_jpeg(outside)

    class SymlinkWanx(FakeWanx):
        def generate(self, prompt, output_path, **kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).symlink_to(outside)
            return output_path, 0.1

    with pytest.raises(ProviderMediaExecutionError, match="private artifact input"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=SymlinkWanx,
        )

    class OversizedWanx(FakeWanx):
        def generate(self, prompt, output_path, **kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"x" * 32)
            return output_path, 0.1

    with pytest.raises(ProviderMediaExecutionError, match="private artifact input"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch2",
            bounds=SyncArtifactBounds(image_max_bytes=16, audio_max_bytes=1024 * 1024),
            image_model_factory=OversizedWanx,
        )



def test_rejects_playlist_image_input_before_transcode_without_network(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError, execute_sync_artifact_to_private_file

    class PlaylistWanx(FakeWanx):
        def generate(self, prompt, output_path, **kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text("#EXTM3U\nhttp://127.0.0.1:1/evil.ts\n", encoding="utf-8")
            return output_path, 0.1

    with pytest.raises(ProviderMediaExecutionError, match="metadata is invalid|private artifact input"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=PlaylistWanx,
        )


def test_rejects_image_pixel_bounds_before_transcode(tmp_path, media_root, monkeypatch):
    from src.models import provider_media_execution as execution
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError, execute_sync_artifact_to_private_file

    def fail_decode(*_args, **_kwargs):
        raise AssertionError("pixel bounds must be checked before full image decode")

    monkeypatch.setattr(execution, "probe_image_file", fail_decode)

    with pytest.raises(ProviderMediaExecutionError, match="dimensions"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024, image_max_pixels=1),
            image_model_factory=FakeWanx,
        )


def test_rejects_audio_duration_bounds_before_transcode(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError, execute_sync_artifact_to_private_file

    with pytest.raises(ProviderMediaExecutionError, match="duration"):
        execute_sync_artifact_to_private_file(
            audio_record(),
            dashscope_authority(media_kind="audio", model="cosyvoice-v3-flash"),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(
                image_max_bytes=1024 * 1024,
                audio_max_bytes=1024 * 1024,
                audio_max_duration_seconds=0.001,
            ),
            tts_processor_factory=FakeTTS,
        )


def test_rejects_truncated_but_decodable_mp3_output(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError, execute_sync_artifact_to_private_file

    class ShortTTS(FakeTTS):
        def execute(self, prepared, output_path):
            write_wav_frames(Path(output_path), 100)
            return output_path, 0.1, "request-id"

    with pytest.raises(ProviderMediaExecutionError, match="boundary|duration"):
        execute_sync_artifact_to_private_file(
            audio_record(),
            dashscope_authority(media_kind="audio", model="cosyvoice-v3-flash"),
            output_root=media_root.root,
            scratch_dir=tmp_path / "scratch",
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=250),
            tts_processor_factory=ShortTTS,
        )


def test_transcodes_use_resolved_bundled_ffmpeg_path(tmp_path, media_root, monkeypatch):
    from src.models import provider_media_execution as execution
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    real_ffmpeg = execution.get_ffmpeg_path() or execution.shutil.which("ffmpeg")
    real_ffprobe = execution.shutil.which("ffprobe")
    if real_ffmpeg is None or real_ffprobe is None:
        pytest.skip("ffmpeg/ffprobe are required for bundled ffmpeg path regression")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    bundled_ffmpeg = bundle / "ffmpeg"
    bundled_ffprobe = bundle / "ffprobe"
    bundled_ffmpeg.write_text("# bundled ffmpeg placeholder\n", encoding="utf-8")
    bundled_ffprobe.write_text("# bundled ffprobe placeholder\n", encoding="utf-8")

    seen_executables: list[str] = []
    real_run = execution.subprocess.run

    def spy_run(cmd, *args, **kwargs):
        if cmd:
            seen_executables.append(str(cmd[0]))
            if cmd[0] == str(bundled_ffmpeg):
                cmd = [real_ffmpeg, *cmd[1:]]
            elif cmd[0] == str(bundled_ffprobe):
                cmd = [real_ffprobe, *cmd[1:]]
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(execution, "get_ffmpeg_path", lambda: str(bundled_ffmpeg))
    monkeypatch.setattr(execution.subprocess, "run", spy_run)

    image_result = execute_sync_artifact_to_private_file(
        image_record(),
        dashscope_authority(),
        output_root=media_root.root,
        scratch_dir=tmp_path / "image-scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        image_model_factory=FakeWanx,
    )
    audio_result = execute_sync_artifact_to_private_file(
        audio_record(),
        dashscope_authority(media_kind="audio", model="cosyvoice-v3-flash"),
        output_root=media_root.root,
        scratch_dir=tmp_path / "audio-scratch",
        bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
        tts_processor_factory=FakeTTS,
    )

    assert image_result.path.read_bytes().startswith(b"\x89PNG")
    assert audio_result.path.is_file()
    assert str(bundled_ffmpeg) in seen_executables
    assert str(bundled_ffprobe) in seen_executables


def test_transcode_timeout_cleans_private_outputs(tmp_path, media_root, monkeypatch):
    from subprocess import TimeoutExpired
    from src.models import provider_media_execution as execution
    from src.models.provider_media_execution import SyncArtifactBounds, ProviderMediaExecutionError, execute_sync_artifact_to_private_file

    scratch = tmp_path / "scratch"
    real_run = execution.subprocess.run

    def timeout_transcode(cmd, **kwargs):
        if cmd and Path(cmd[0]).name == "ffmpeg" and "-f" in cmd and "image2" in cmd:
            raise TimeoutExpired(cmd, timeout=kwargs.get("timeout"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(execution.subprocess, "run", timeout_transcode)
    with pytest.raises(ProviderMediaExecutionError, match="converted"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=scratch,
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=FakeWanx,
        )

    assert not any(path.is_file() for path in scratch.rglob("*"))


def test_provider_failure_cleans_private_outputs(tmp_path, media_root):
    from src.models.provider_media_execution import SyncArtifactBounds, execute_sync_artifact_to_private_file

    class FailingWanx(FakeWanx):
        def generate(self, prompt, output_path, **kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"partial")
            raise RuntimeError("provider failed")

    scratch = tmp_path / "scratch"
    with pytest.raises(RuntimeError, match="provider failed"):
        execute_sync_artifact_to_private_file(
            image_record(),
            dashscope_authority(),
            output_root=media_root.root,
            scratch_dir=scratch,
            bounds=SyncArtifactBounds(image_max_bytes=1024 * 1024, audio_max_bytes=1024 * 1024),
            image_model_factory=FailingWanx,
        )

    assert not any(path.is_file() for path in scratch.rglob("*"))
