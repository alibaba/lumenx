"""Branch provenance travels as verified creative material, never authority."""

from copy import deepcopy
import hashlib
import io
import json
from typing import Any
import zipfile

import pytest

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.project_package import (
    AtelierProjectPackageError, build_package, prepare_import,
)
from src.apps.atelier.storage import AtelierStorage


def _project(root, *, kind="video", recipe_status="available"):
    output = b"branch source bytes"
    ref = f"uploads/source.{'mp4' if kind == 'video' else 'png' if kind == 'image' else 'wav'}"
    (root / "uploads").mkdir(exist_ok=True)
    (root / ref).write_bytes(output)
    source = {
        "schema_version": 1, "origin_project_id": "historical-project",
        "origin_node_id": "deleted-parent", "origin_candidate_id": "historical-candidate",
        "media_kind": kind, "media_url": ref,
        "media_sha256": hashlib.sha256(output).hexdigest(), "media_size_bytes": len(output),
        "recipe_status": recipe_status,
    }
    draft = {
        "schema_version": 2, "input_contract_version": "ordered-media-v1",
        "model_id": "wan/wan3.0-video#video", "mode": "r2v",
        "params": {"audio": False, "duration": -1, "seed": 0}, "batch_size": 1,
        "media": [
            {"role": "reference_image", "ordinal": 0, "node_id": "ref-image", "media_url": "uploads/input.png"},
            {"role": "reference_audio", "ordinal": 0, "node_id": "ref-audio", "media_url": "uploads/input.wav"},
        ],
    }
    refs = [AtelierNode.model_validate(dict(id="ref-image", project_id="p", type="image", status="completed", media_urls=["uploads/input.png"])),
            AtelierNode.model_validate(dict(id="ref-audio", project_id="p", type="audio", status="completed", media_urls=["uploads/input.wav"]))]
    if kind == "image":
        draft = {"schema_version": 3, "media_kind": "image", "model_id": "wan/wan2.7-image-pro#image",
                 "mode": "i2i", "params": {"watermark": False, "seed": 0}, "batch_size": 1,
                 "media": [draft["media"][0]]}
        refs = refs[:1]
    elif kind == "audio":
        draft = {"schema_version": 3, "media_kind": "audio", "voice": "longanyang", "params": {"volume": 0}}
        refs = []
    for node in refs:
        (root / node.media_urls[0]).write_bytes(f"input {node.type}".encode())
    data: dict[str, Any] = {"branch_source": source}
    if recipe_status == "available":
        data.update(generation_draft=draft, reference_node_ids=[node.id for node in refs])
        for node in refs:
            data[f"reference_{node.type}_urls"] = list(node.media_urls)
    else:
        refs = []
    return AtelierProject.model_validate(dict(id="p", title="Independent branch", created_at=1, updated_at=1,
        nodes=[dict(id="branch", project_id="p", type=kind, status="draft", prompt="Original frozen text" if recipe_status == "available" else "", data=data), *refs]))


def _copy(project, root):
    artifact = build_package(project, data_dir=root, source_storage_id="source")
    return prepare_import(artifact.data, data_dir=root, target_storage_id="target",
                          id_factory=lambda kind: f"copy-{kind}", now_fn=lambda: 9)


@pytest.mark.parametrize("kind", ["video", "image", "audio"])
def test_branch_copy_preserves_historical_provenance_but_remaps_creative_inputs(tmp_path, kind):
    original = _project(tmp_path, kind=kind)
    before = original.model_dump(mode="json")
    prepared = _copy(original, tmp_path)
    branch = prepared.project.nodes[0]
    source = branch.data["branch_source"]
    assert {key: value for key, value in source.items() if key != "media_url"} == {
        key: value for key, value in original.nodes[0].data["branch_source"].items() if key != "media_url"}
    assert source["media_url"].startswith("uploads/imported/copy-project/")
    assert hashlib.sha256((tmp_path / source["media_url"]).read_bytes()).hexdigest() == source["media_sha256"]
    assert branch.status == "draft" and branch.media_urls == []
    assert branch.prompt == "Original frozen text"
    assert "candidates" not in branch.data and "selected_candidate_id" not in branch.data
    assert prepared.project.generation_attempts == prepared.project.generation_request_receipts == []
    assert branch.data["reference_node_ids"] == [node.id for node in prepared.project.nodes[1:]]
    expected = deepcopy(original.nodes[0].data["generation_draft"])
    for media in expected.get("media", []):
        media["node_id"] = prepared.id_map["nodes"][media["node_id"]]
        media["media_url"] = prepared.media[media["media_url"]]
    assert branch.data["generation_draft"] == expected
    assert original.model_dump(mode="json") == before
    store = AtelierStorage(tmp_path)
    try:
        store.create_project(prepared.project)
    finally:
        store.close()
    reopened = AtelierStorage(tmp_path)
    try:
        saved = reopened.get_project(prepared.project.id)
        assert saved.nodes[0].data == branch.data
    finally:
        reopened.close()
    second = _copy(prepared.project, tmp_path)
    assert second.project.nodes[0].data["branch_source"]["origin_node_id"] == "deleted-parent"


@pytest.mark.parametrize("status", ["unavailable_imported", "unavailable_legacy", "unavailable_inputs", "unsupported"])
def test_source_only_branch_stays_source_only_after_independent_copy(tmp_path, status):
    copied = _copy(_project(tmp_path, recipe_status=status), tmp_path).project.nodes[0]
    assert copied.prompt == ""
    assert "generation_draft" not in copied.data
    assert copied.data["branch_source"]["recipe_status"] == status


def test_first_frame_branch_keeps_v1_recipe_and_remaps_materialized_input(tmp_path):
    project = _project(tmp_path)
    project.nodes = project.nodes[:2]
    project.nodes[0].data = {
        "branch_source": project.nodes[0].data["branch_source"],
        "reference_node_ids": ["ref-image"], "reference_image_urls": ["uploads/input.png"],
        "generation_draft": {"schema_version": 1, "model_id": "wan/wan2.7-video#i2v",
                             "mode": "i2v", "params": {"seed": 0, "watermark": False}},
    }
    copied = _copy(project, tmp_path).project
    assert copied.nodes[0].data["generation_draft"] == project.nodes[0].data["generation_draft"]
    assert copied.nodes[0].data["reference_node_ids"] == [copied.nodes[1].id]
    assert copied.nodes[0].data["reference_image_urls"] == copied.nodes[1].media_urls


def _mutate_package(package, mutate):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            value = source.read(name)
            if name == "project.json":
                payload = json.loads(value)
                mutate(payload["nodes"][0]["data"]["branch_source"])
                value = json.dumps(payload).encode()
            target.writestr(name, value)
    return output.getvalue()


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"origin_project_id": " "},
    {"origin_node_id": "\ud800"}, {"origin_candidate_id": None}, {"media_kind": "text"},
    {"media_sha256": "x" * 64}, {"media_size_bytes": True}, {"media_size_bytes": 0},
    {"recipe_status": "guessed"}, {"extra": "unexpected"}, {"credential_ref": "forged"},
    {"credential_ref": None}, {"media_kind": "audio"},
    {"media_url": "https://example.test/source.mp4"}, {"media_url": "uploads/../secret.mp4"},
])
def test_branch_package_rejects_invalid_source_before_staging(tmp_path, change):
    project = _project(tmp_path)
    artifact = build_package(project, data_dir=tmp_path, source_storage_id="source")
    package = _mutate_package(artifact.data, lambda source: source.update(change))
    with pytest.raises(AtelierProjectPackageError):
        prepare_import(package, data_dir=tmp_path, target_storage_id="target")
    assert not (tmp_path / "uploads/imported").exists()
    project.nodes[0].data["branch_source"].update(change)
    with pytest.raises(AtelierProjectPackageError):
        build_package(project, data_dir=tmp_path, source_storage_id="source")


@pytest.mark.parametrize("missing", ["schema_version", "origin_project_id", "media_url", "recipe_status"])
def test_branch_package_requires_every_source_field(tmp_path, missing):
    project = _project(tmp_path)
    artifact = build_package(project, data_dir=tmp_path, source_storage_id="source")
    package = _mutate_package(artifact.data, lambda source: source.pop(missing))
    with pytest.raises(AtelierProjectPackageError):
        prepare_import(package, data_dir=tmp_path, target_storage_id="target")
    assert not (tmp_path / "uploads/imported").exists()


@pytest.mark.parametrize("field,value", [("media_sha256", "a" * 64), ("media_size_bytes", 1000)])
def test_branch_package_binds_source_proof_to_actual_media(tmp_path, field, value):
    project = _project(tmp_path)
    artifact = build_package(project, data_dir=tmp_path, source_storage_id="source")
    package = _mutate_package(artifact.data, lambda source: source.update({field: value}))
    with pytest.raises(AtelierProjectPackageError, match="branch source"):
        prepare_import(package, data_dir=tmp_path, target_storage_id="target")
    assert not (tmp_path / "uploads/imported").exists()
    project.nodes[0].data["branch_source"].update({field: value})
    with pytest.raises(AtelierProjectPackageError, match="branch source"):
        build_package(project, data_dir=tmp_path, source_storage_id="source")


def test_branch_package_rejects_changed_source_bytes_even_when_new_manifest_is_self_consistent(tmp_path):
    project = _project(tmp_path)
    source = project.nodes[0].data["branch_source"]
    (tmp_path / source["media_url"]).write_bytes(b"different local media")
    with pytest.raises(AtelierProjectPackageError, match="branch source"):
        build_package(project, data_dir=tmp_path, source_storage_id="source")
