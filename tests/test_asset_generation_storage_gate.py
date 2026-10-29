"""Internal asset batches must stop before new charges after storage fails."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen import assets
from src.apps.comic_gen.models import Character, GenerationStatus, Prop, Scene
from src.utils.json_storage import StorageError


KINDS = ["reference_sheet", "full_body", "three_view", "headshot", "all", "scene", "prop"]


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    available = [True]
    paths = []
    behavior = [None]

    def guard():
        if not available[0]:
            raise StorageError("storage unavailable; restart required")

    def generate(prompt, output_path, **kwargs):
        path = Path(output_path)
        paths.append(path)
        if behavior[0]:
            behavior[0](len(paths))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"paid-output")
        return str(path), 0

    monkeypatch.setattr(assets, "WanxImageModel", lambda config: SimpleNamespace(generate=generate))
    monkeypatch.setattr(assets.time, "sleep", lambda _: None)
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", lambda: SimpleNamespace(is_configured=False))
    config = {"output_dir": str(tmp_path / "assets"), "output_root": str(tmp_path)}
    generator = assets.AssetGenerator(config)
    # Assignment lets the red test reach the old paid batch loop rather than
    # stopping merely because the constructor did not yet accept the callback.
    generator.before_generation = guard
    reference = tmp_path / "assets/characters/master.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"input")
    return generator, config, available, paths, behavior, guard


def _asset(kind):
    if kind == "scene":
        return Scene(id="scene", name="scene", description="test")
    if kind == "prop":
        return Prop(id="prop", name="prop", description="test")
    return Character(id="character", name="character", description="test",
                     full_body_image_url="assets/characters/master.png")


def _generate(generator, kind, item, batch_size):
    if kind == "scene":
        return generator.generate_scene(item, batch_size=batch_size)
    if kind == "prop":
        return generator.generate_prop(item, batch_size=batch_size)
    return generator.generate_character(item, generation_type=kind, batch_size=batch_size)


def _variants(item):
    if isinstance(item, Character):
        return [*item.reference_sheet.image_variants, *item.full_body_asset.variants,
                *item.three_view_asset.variants, *item.headshot_asset.variants]
    return item.image_asset.variants


@pytest.mark.parametrize("kind", KINDS)
def test_failure_during_first_provider_stops_batch_and_retains_paid_file(fixture, kind):
    generator, _, available, paths, behavior, _ = fixture
    behavior[0] = lambda count: available.__setitem__(0, False)
    item = _asset(kind)
    with pytest.raises(StorageError, match="restart"):
        _generate(generator, kind, item, batch_size=3)
    assert len(paths) == 1
    assert paths[0].read_bytes() == b"paid-output"
    assert _variants(item) == []
    assert item.status != GenerationStatus.COMPLETED


@pytest.mark.parametrize("kind", KINDS)
def test_storage_error_from_provider_is_not_swallowed_or_retried(fixture, kind):
    generator, _, _, paths, behavior, _ = fixture
    def fail(count):
        raise StorageError("cannot save provider receipt")
    behavior[0] = fail
    item = _asset(kind)
    with pytest.raises(StorageError, match="receipt"):
        _generate(generator, kind, item, batch_size=3)
    assert len(paths) == 1
    assert _variants(item) == []


def test_already_failed_guard_does_not_mutate_asset_or_call_provider(fixture):
    generator, _, available, paths, _, _ = fixture
    available[0] = False
    item = _asset("full_body")
    before = item.model_dump()
    with pytest.raises(StorageError):
        _generate(generator, "full_body", item, batch_size=1)
    assert paths == []
    assert item.model_dump() == before


def test_callback_is_explicit_constructor_argument(fixture):
    _, config, available, paths, _, guard = fixture
    generator = assets.AssetGenerator(config, before_generation=guard)
    available[0] = False
    with pytest.raises(StorageError):
        generator.generate_scene(_asset("scene"))
    assert paths == []


def test_standalone_generator_without_callback_keeps_batch_behavior(fixture):
    _, config, _, paths, _, _ = fixture
    generator = assets.AssetGenerator(config)
    item = generator.generate_scene(_asset("scene"), batch_size=2)
    assert len(paths) == 2
    assert len(item.image_asset.variants) == 2
    assert item.status == GenerationStatus.COMPLETED


UPLOAD_KINDS = ["reference_sheet", "full_body", "three_view", "headshot", "scene", "prop"]


@pytest.mark.parametrize("kind", UPLOAD_KINDS)
@pytest.mark.parametrize("failure_stage", ["before_upload", "upload", "after_upload", "after_upload_error"])
def test_upload_storage_failure_stops_batch_without_publishing_remote_url(
    fixture, monkeypatch, kind, failure_stage,
):
    generator, _, _, paths, _, _ = fixture
    failure = StorageError("upload storage no longer writable", commit_uncertain=True)
    failed = [False]
    uploads = []

    def guard():
        if failed[0]:
            raise failure

    class Uploader:
        @property
        def is_configured(self):
            # Simulate closure after the provider's result check and before
            # the optional remote upload begins.
            if failure_stage == "before_upload":
                failed[0] = True
            return True

        def upload_file(self, path, **kwargs):
            uploads.append(Path(path))
            if failure_stage == "upload":
                raise failure
            failed[0] = True
            if failure_stage == "after_upload_error":
                raise OSError("mirror failed after storage closed")
            return "assets/remote-uploaded.png"

    generator.before_generation = guard
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", Uploader)
    item = _asset(kind)
    with pytest.raises(StorageError) as raised:
        # A last-upload exception must check the runtime too; there is no next
        # provider call whose initial check could hide a missing final guard.
        _generate(generator, kind, item, batch_size=1 if failure_stage == "after_upload_error" else 3)
    assert raised.value is failure
    assert len(paths) == 1, "an upload storage error must not permit another paid call"
    assert len(uploads) == (0 if failure_stage == "before_upload" else 1)
    assert paths[0].read_bytes() == b"paid-output"
    assert all(variant.url != "assets/remote-uploaded.png" for variant in _variants(item))
    assert "assets/remote-uploaded.png" not in str(item.model_dump())
    assert item.status != GenerationStatus.COMPLETED


@pytest.mark.parametrize("kind", UPLOAD_KINDS)
def test_optional_upload_failure_keeps_local_results_and_does_not_retry_upload(
    fixture, monkeypatch, kind,
):
    generator, _, _, paths, _, _ = fixture
    uploads = []

    def upload(path, **kwargs):
        uploads.append(Path(path))
        raise OSError("synthetic optional mirror failure")

    monkeypatch.setattr(
        "src.utils.oss_utils.OSSImageUploader",
        lambda: SimpleNamespace(is_configured=True, upload_file=upload),
    )
    item = _generate(generator, kind, _asset(kind), batch_size=2)
    assert len(paths) == len(uploads) == 2
    assert uploads == paths
    assert all(path.read_bytes() == b"paid-output" for path in paths)
    assert len(_variants(item)) == 2
    assert item.status == GenerationStatus.COMPLETED
