import math

import pytest
from pydantic import ValidationError

from src.apps.comic_gen.models import ProviderExecutionRecord, ProviderRequestReceipt, Script, VideoTask
from src.utils.request_identity import (
    REQUEST_IDENTITY_CONTRACT_VERSION,
    canonical_request_json,
    fingerprint_request,
    validate_client_request_id,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def test_request_identity_canonical_json_sorts_object_keys_and_preserves_array_order():
    left = canonical_request_json(
        {"z": [3, 2, 1], "a": {"b": 2, "a": 1}},
        entrypoint="studio.storyboard_r2v",
    )
    right = canonical_request_json(
        {"a": {"a": 1, "b": 2}, "z": [3, 2, 1]},
        entrypoint="studio.storyboard_r2v",
    )
    changed_array = canonical_request_json(
        {"a": {"a": 1, "b": 2}, "z": [1, 2, 3]},
        entrypoint="studio.storyboard_r2v",
    )

    assert left == right
    assert left != changed_array
    assert left == '{"entrypoint":"studio.storyboard_r2v","request":{"a":{"a":1,"b":2},"z":[3,2,1]},"request_contract_version":"request-json-v1"}'


def test_request_identity_hash_includes_entrypoint_and_contract_version():
    request = {"frame_id": "frame-1", "seed": 0}

    base = fingerprint_request(request, entrypoint="studio.storyboard_r2v")

    assert len(base) == 64
    assert base != fingerprint_request(request, entrypoint="studio.other")
    with pytest.raises(ValueError):
        fingerprint_request(request, entrypoint="studio.storyboard_r2v", contract_version="request-json-v2")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({}, {"prompt": None}),
        ({"prompt_extend": False}, {"prompt_extend": None}),
        ({"seed": 0}, {}),
        ({"seed": 0}, {"seed": False}),
        ({"text": " café "}, {"text": "café"}),
        ({"text": "é"}, {"text": "e\u0301"}),
        ({"model": "wan"}, {"model_id": "wan"}),
    ],
)
def test_request_identity_keeps_raw_json_semantics_without_defaults_or_normalization(left, right):
    assert fingerprint_request(left, entrypoint="studio.storyboard_r2v") != fingerprint_request(right, entrypoint="studio.storyboard_r2v")


@pytest.mark.parametrize(
    "bad_request",
    [
        {1: "not a string key"},
        {"nan": math.nan},
        {"inf": math.inf},
        {"nested": {False: "not a string key"}},
        {"bad": object()},
        {"bad": b"bytes"},
        {"bad": "\ud800"},
    ],
)
def test_request_identity_rejects_values_that_are_not_strict_json(bad_request):
    with pytest.raises(ValueError):
        canonical_request_json(bad_request, entrypoint="studio.storyboard_r2v")


def test_validate_client_request_id_accepts_opaque_ascii_ids_and_rejects_bad_values():
    assert validate_client_request_id("abc.DEF_123:retry-1") == "abc.DEF_123:retry-1"
    for value in ["", "x" * 129, "has space", "中文", True]:
        with pytest.raises(ValueError):
            validate_client_request_id(value)


def test_request_identity_requires_string_entrypoint_contract_and_mapping_request():
    with pytest.raises(ValueError):
        canonical_request_json({}, entrypoint="")
    with pytest.raises(ValueError):
        canonical_request_json({}, entrypoint="studio.storyboard_r2v", contract_version="")
    with pytest.raises(ValueError):
        canonical_request_json([], entrypoint="studio.storyboard_r2v")


def receipt(**overrides):
    data = {
        "schema_version": 1,
        "request_contract_version": REQUEST_IDENTITY_CONTRACT_VERSION,
        "entrypoint": "studio.storyboard_r2v",
        "client_request_id": "client-1",
        "frame_id": "frame-1",
        "request_fingerprint": HASH_A,
        "execution_fingerprint": HASH_B,
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "created_at": 1.0,
    }
    data.update(overrides)
    return ProviderRequestReceipt(**data)


def provider_record(**overrides):
    data = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "phase": "accepted",
        "provider_model_id": "wan2.6-i2v-flash",
        "intent": {},
        "media": [],
        "scope": {"provider": "dashscope", "backend": "dashscope", "gateway": "dashscope", "base_url": "https://dashscope.aliyuncs.com"},
        "credential_ref": "cred-1",
        "fingerprint": HASH_B,
    }
    data.update(overrides)
    return ProviderExecutionRecord(**data)


def test_provider_request_receipt_accepts_minimal_schema_and_script_defaults_empty_ledger():
    item = receipt()
    script = Script(id="p", title="Project", original_text="text", created_at=1.0, updated_at=1.0)

    assert item.schema_version == 1
    assert item.entrypoint == "studio.storyboard_r2v"
    assert script.provider_request_receipts == []


def test_provider_request_receipt_rejects_bad_versions_and_hashes():
    with pytest.raises(ValidationError):
        receipt(schema_version=True)
    with pytest.raises(ValidationError):
        receipt(schema_version=2)
    with pytest.raises(ValidationError):
        receipt(request_contract_version="request-json-v2")
    with pytest.raises(ValidationError):
        receipt(client_request_id="has space")
    with pytest.raises(ValidationError):
        receipt(created_at=True)
    with pytest.raises(ValidationError):
        receipt(created_at=math.nan)
    with pytest.raises(ValidationError):
        receipt(request_fingerprint="A" * 64)
    with pytest.raises(ValidationError):
        receipt(execution_fingerprint="a" * 63)


@pytest.mark.parametrize(
    "field",
    [
        "request_contract_version",
        "entrypoint",
        "frame_id",
        "task_id",
        "attempt_id",
    ],
)
def test_provider_request_receipt_requires_non_empty_strict_strings(field):
    with pytest.raises(ValidationError):
        receipt(**{field: ""})
    with pytest.raises(ValidationError):
        receipt(**{field: True})


def test_script_rejects_duplicate_receipt_keys_within_project():
    with pytest.raises(ValidationError):
        Script(
            id="p",
            title="Project",
            original_text="text",
            created_at=1.0,
            updated_at=1.0,
            provider_request_receipts=[receipt(), receipt(frame_id="frame-2", request_fingerprint=HASH_C)],
        )


def test_script_allows_same_client_request_id_for_different_entrypoints():
    script = Script(
        id="p",
        title="Project",
        original_text="text",
        created_at=1.0,
        updated_at=1.0,
        provider_request_receipts=[
            receipt(),
            receipt(
                entrypoint="studio.other",
                frame_id="frame-2",
                request_fingerprint=HASH_C,
                task_id="task-2",
                attempt_id="attempt-2",
            ),
        ],
    )

    assert len(script.provider_request_receipts) == 2


def test_script_rejects_receipt_relation_that_disagrees_with_existing_task_execution():
    with pytest.raises(ValidationError):
        Script(
            id="p",
            title="Project",
            original_text="text",
            created_at=1.0,
            updated_at=1.0,
            video_tasks=[
                VideoTask(
                    id="task-1",
                    project_id="p",
                    frame_id="frame-1",
                    image_url="uploads/a.png",
                    prompt="prompt",
                    status="pending",
                    provider_execution=provider_record(attempt_id="different-attempt"),
                )
            ],
            provider_request_receipts=[receipt()],
        )


def test_script_rejects_receipt_relation_with_wrong_project_or_frame():
    base_task = VideoTask(
        id="task-1",
        project_id="other-project",
        frame_id="frame-1",
        image_url="uploads/a.png",
        prompt="prompt",
        status="pending",
        provider_execution=provider_record(),
    )
    with pytest.raises(ValidationError):
        Script(
            id="p",
            title="Project",
            original_text="text",
            created_at=1.0,
            updated_at=1.0,
            video_tasks=[base_task],
            provider_request_receipts=[receipt()],
        )

    with pytest.raises(ValidationError):
        Script(
            id="p",
            title="Project",
            original_text="text",
            created_at=1.0,
            updated_at=1.0,
            video_tasks=[base_task.model_copy(update={"project_id": "p", "frame_id": "frame-2"})],
            provider_request_receipts=[receipt()],
        )


def test_script_rejects_more_than_one_receipt_for_same_task():
    task = VideoTask(
        id="task-1",
        project_id="p",
        frame_id="frame-1",
        image_url="uploads/a.png",
        prompt="prompt",
        status="pending",
        provider_execution=provider_record(),
    )
    with pytest.raises(ValidationError):
        Script(
            id="p",
            title="Project",
            original_text="text",
            created_at=1.0,
            updated_at=1.0,
            video_tasks=[task],
            provider_request_receipts=[
                receipt(),
                receipt(entrypoint="studio.other", client_request_id="client-2", request_fingerprint=HASH_C),
            ],
        )


def test_script_allows_receipt_tombstone_when_task_is_missing():
    script = Script(
        id="p",
        title="Project",
        original_text="text",
        created_at=1.0,
        updated_at=1.0,
        provider_request_receipts=[receipt()],
    )

    assert script.provider_request_receipts[0].task_id == "task-1"
