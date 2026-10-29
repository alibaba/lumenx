"""Characterize the legacy reference-sheet contract before pure preparation extraction."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen import assets
from src.apps.comic_gen.models import Character, GenerationStatus
from src.apps.comic_gen.pipeline import ComicGenPipeline

DEFAULT_PROMPT = (
    "Character reference sheet for Alice. red coat. Multiple views: front, side, back. "
    "Clean background, studio lighting."
)


PROMPT_CASES = [
    (None, None, None, DEFAULT_PROMPT, "1024*1024"),
    ("", "", "", DEFAULT_PROMPT, "1024*1024"),
    ("", "watercolor", None, DEFAULT_PROMPT + ", watercolor", "1024*1024"),
    ("", "studio lighting", None, DEFAULT_PROMPT, "1024*1024"),
    ("", "Studio Lighting", None, DEFAULT_PROMPT + ", Studio Lighting", "1024*1024"),
    (
        "my watercolor reference",
        "watercolor",
        "768*1024",
        "my watercolor reference",
        "768*1024",
    ),
    ("custom", "watercolor", "1024*768", "custom, watercolor", "1024*768"),
    ("custom", None, "legacy-size", "custom", "legacy-size"),
    ("custom", "", None, "custom", "1024*1024"),
    (" ", "", " ", " ", " "),
]


def character(*, selected=True, with_sheet=True):
    return Character.model_validate(
        {
            "id": "hero",
            "name": "Alice",
            "description": "red coat",
            "voice_id": "voice-original",
            "image_url": "uploads/selected.png",
            "status": "completed",
            "full_body_prompt": "legacy prompt",
            "full_body_image_url": "uploads/body.png",
            "reference_sheet": {
                "selected_image_id": "old" if selected else None,
                "image_variants": [
                    {
                        "id": "old",
                        "url": "uploads/selected.png",
                        "created_at": 1,
                        "is_favorited": True,
                    }
                ],
                "selected_video_id": "motion-old",
                "video_variants": [
                    {"id": "motion-old", "url": "video/old.mp4", "created_at": 1}
                ],
                "image_prompt": "saved-sheet-prompt",
                "image_updated_at": 9,
            }
            if with_sheet
            else None,
        }
    )


@pytest.fixture
def generator(tmp_path, monkeypatch):
    state = SimpleNamespace(
        calls=[], uploads=[], uploader_options=[], failures=set(), mirror="disabled"
    )
    root = tmp_path / "output"

    def generate(prompt, output_path, **kwargs):
        call = {"prompt": prompt, "path": Path(output_path), "kwargs": kwargs}
        state.calls.append(call)
        call["path"].write_bytes(b"synthetic-paid-image")
        if len(state.calls) - 1 in state.failures:
            raise RuntimeError(f"synthetic slot {len(state.calls)} failed")
        return output_path, 0.0

    def upload(path, **kwargs):
        state.uploads.append((Path(path), kwargs))
        if state.mirror == "error":
            raise OSError("synthetic optional mirror unavailable")
        if state.mirror == "success":
            return f"assets/characters/mirror-{len(state.uploads)}.png"
        return None

    def uploader(**kwargs):
        state.uploader_options.append(kwargs)
        return SimpleNamespace(
            is_configured=state.mirror != "disabled", upload_file=upload
        )

    monkeypatch.setattr(
        assets,
        "WanxImageModel",
        lambda *args, **kwargs: SimpleNamespace(generate=generate),
    )
    monkeypatch.setattr("src.utils.oss_utils.OSSImageUploader", uploader)
    monkeypatch.setattr(assets.time, "sleep", lambda seconds: None)
    return assets.AssetGenerator(
        {"output_dir": str(root / "assets"), "output_root": str(root)}, env={}
    ), state


@pytest.mark.parametrize("prompt,positive,size,expected,expected_size", PROMPT_CASES)
def test_legacy_final_prompt_size_and_negative_are_exact(
    generator, prompt, positive, size, expected, expected_size
):
    model, state = generator
    target = character()
    result = model.generate_character(
        target,
        generation_type="reference_sheet",
        prompt=prompt,
        positive_prompt=positive,
        negative_prompt="user negative, global negative",
        size=size,
        model_name="wan2.7-image-pro",
        i2i_model_name="must-not-route",
    )
    assert result is target
    assert state.calls[0]["prompt"] == expected
    assert state.calls[0]["kwargs"] == {
        "negative_prompt": "user negative, global negative",
        "model_name": "wan2.7-image-pro",
        "size": expected_size,
    }
    assert target.reference_sheet is not None
    assert target.reference_sheet.image_variants[-1].prompt_used == expected
    assert target.reference_sheet.image_prompt == "saved-sheet-prompt"
    assert target.status == GenerationStatus.COMPLETED


def params(**updates):
    values = dict(
        aspect_ratio=None,
        apply_style=True,
        negative_prompt="user negative",
        style_prompt=None,
        style_preset=None,
        batch_size=6,
        model_name=None,
        generation_type="reference_sheet",
        prompt=None,
    )
    values.update(updates)
    return values


def view(style=None):
    model_settings = SimpleNamespace(
        character_aspect_ratio="9:16",
        t2i_model="wan2.7-image-pro",
        i2i_model="wan2.6-image",
    )
    root = SimpleNamespace(
        model_settings=model_settings,
        art_direction=SimpleNamespace(style_config=style) if style else None,
        style_preset="legacy preset",
        style_prompt="legacy supplement",
    )
    return root, None, None


@pytest.mark.parametrize(
    "ratio,size",
    [
        (None, "576*1024"),
        ("9:16", "576*1024"),
        ("3:4", "768*1024"),
        ("1:1", "1024*1024"),
        ("4:3", "1024*768"),
        ("16:9", "1024*576"),
    ],
)
def test_pipeline_preparation_supplies_portrait_default_and_preserves_all_slots(
    generator, ratio, size
):
    model, state = generator
    options = ComicGenPipeline._asset_image_options(
        view(), "character", params(aspect_ratio=ratio)
    )
    assert options["size"] == size and options["batch_size"] == 6
    model.generate_character(character(), **options)
    assert len(state.calls) == 6
    assert all(call["kwargs"]["size"] == size for call in state.calls)
    assert all(
        call["prompt"] == DEFAULT_PROMPT + ", legacy preset style, legacy supplement"
        for call in state.calls
    )
    assert all(
        call["kwargs"]["negative_prompt"] == "user negative" for call in state.calls
    )


@pytest.mark.parametrize("positive", [None, "", "watercolor"])
@pytest.mark.parametrize("apply_style", [False, True])
def test_effective_style_and_negative_from_pipeline_are_not_silently_changed(
    generator, positive, apply_style
):
    model, state = generator
    options = ComicGenPipeline._asset_image_options(
        view({"positive_prompt": positive, "negative_prompt": "global negative"}),
        "character",
        params(apply_style=apply_style, batch_size=1),
    )
    assert options["positive_prompt"] == (positive if apply_style else "")
    assert options["negative_prompt"] == (
        "user negative, global negative" if apply_style else "user negative"
    )
    model.generate_character(character(), **options)
    assert state.calls[0]["prompt"] == DEFAULT_PROMPT + (
        ", watercolor" if apply_style and positive else ""
    )
    assert state.calls[0]["kwargs"]["negative_prompt"] == options["negative_prompt"]


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("failed_slots", [set(), {0, 2, 4}])
def test_batch_above_four_keeps_order_selection_and_partial_success(
    generator, selected, failed_slots
):
    model, state = generator
    target = character(selected=selected)
    before = target.model_dump(mode="json")
    state.failures = failed_slots
    model.generate_character(target, generation_type="reference_sheet", batch_size=6)
    assert len(state.calls) == 6
    assert all(
        call["path"].read_bytes() == b"synthetic-paid-image" for call in state.calls
    )
    sheet = target.reference_sheet
    assert sheet is not None
    expected_paths = [
        str(call["path"].relative_to(model.output_root))
        for index, call in enumerate(state.calls)
        if index not in failed_slots
    ]
    assert [variant.url for variant in sheet.image_variants] == [
        "uploads/selected.png",
        *expected_paths,
    ]
    if selected:
        assert (
            sheet.selected_image_id == "old"
            and target.image_url == "uploads/selected.png"
        )
    else:
        assert (
            sheet.selected_image_id == sheet.image_variants[1].id
            and target.image_url == expected_paths[0]
        )
    assert target.status == GenerationStatus.COMPLETED
    after = target.model_dump(mode="json")
    for key in set(before) - {"reference_sheet", "status", "image_url"}:
        assert after[key] == before[key]
    for key in set(before["reference_sheet"]) - {"image_variants", "selected_image_id"}:
        assert after["reference_sheet"][key] == before["reference_sheet"][key]


@pytest.mark.parametrize("with_sheet", [False, True])
def test_total_failure_keeps_existing_candidates_and_selection(generator, with_sheet):
    model, state = generator
    target = character(with_sheet=with_sheet)
    before = target.model_dump(mode="json")
    state.failures = {0, 1, 2}
    with pytest.raises(RuntimeError, match="synthetic slot 3 failed"):
        model.generate_character(
            target, generation_type="reference_sheet", batch_size=3
        )
    assert len(state.calls) == 3 and not state.uploads
    assert target.status == GenerationStatus.FAILED
    after = target.model_dump(mode="json")
    assert {key: value for key, value in after.items() if key != "status"} == {
        key: value for key, value in before.items() if key != "status"
    }
    assert all(call["path"].exists() for call in state.calls)


@pytest.mark.parametrize("mirror", ["disabled", "none", "error", "success"])
@pytest.mark.parametrize("selected", [False, True])
def test_optional_oss_only_changes_successful_mirror_urls(generator, mirror, selected):
    model, state = generator
    state.mirror = mirror
    target = character(selected=selected)
    model.generate_character(target, generation_type="reference_sheet", batch_size=2)
    assert target.status == GenerationStatus.COMPLETED and len(state.calls) == 2
    assert len(state.uploads) == (0 if mirror == "disabled" else 2)
    assert target.reference_sheet is not None
    for index, call in enumerate(state.calls):
        local = str(call["path"].relative_to(model.output_root))
        expected = (
            f"assets/characters/mirror-{index + 1}.png"
            if mirror == "success"
            else local
        )
        assert target.reference_sheet.image_variants[index + 1].url == expected
        assert call["path"].read_bytes() == b"synthetic-paid-image"
    assert target.image_url == (
        "uploads/selected.png"
        if selected
        else target.reference_sheet.image_variants[1].url
    )
    assert all(
        options == {"env": {}, "output_root": model.output_root}
        for options in state.uploader_options
    )
    assert all(
        options == {"sub_path": "assets/characters"} for _, options in state.uploads
    )


@pytest.mark.parametrize("prompt,positive,size,expected,expected_size", PROMPT_CASES)
def test_pure_reference_sheet_preparation_matches_characterized_contract(
    prompt, positive, size, expected, expected_size
):
    prepare = getattr(assets, "prepare_character_reference_sheet")
    assert prepare(
        "Alice", "red coat", prompt=prompt, positive_prompt=positive, size=size
    ) == (expected, expected_size)


def test_legacy_reference_sheet_uses_pure_preparation_once_for_entire_batch(
    generator, monkeypatch
):
    model, state = generator
    calls = []

    def prepare(name, description, *, prompt, positive_prompt, size):
        calls.append((name, description, prompt, positive_prompt, size))
        return "prepared prompt", "prepared size"

    monkeypatch.setattr(assets, "prepare_character_reference_sheet", prepare)
    target = character()
    model.generate_character(
        target,
        generation_type="reference_sheet",
        prompt="user prompt",
        positive_prompt="user style",
        negative_prompt="user negative",
        size="user size",
        batch_size=6,
    )
    assert calls == [("Alice", "red coat", "user prompt", "user style", "user size")]
    assert len(state.calls) == 6
    assert all(call["prompt"] == "prepared prompt" for call in state.calls)
    assert all(call["kwargs"]["size"] == "prepared size" for call in state.calls)
    assert all(
        call["kwargs"]["negative_prompt"] == "user negative" for call in state.calls
    )
    assert target.reference_sheet is not None
    assert all(
        variant.prompt_used == "prepared prompt"
        for variant in target.reference_sheet.image_variants[1:]
    )
