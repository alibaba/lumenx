"""Studio image history; loading never grants execution rights.

Historical validity is internal consistency, not permission to project onto a
current asset. Live owner/root/pointer and guard checks belong to the adapter.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_serializer, model_validator

from ...models.provider_execution import ProviderExecutionRecord
from ...models.provider_media_intents import validate_async_image_record_intent, validate_sync_artifact_record_intent
from ...models.provider_recovery import image_query_handle_from_record
from ...models.provider_tasks import ProviderImageQueryContract
from ...utils.media_versions import MediaVersion
from ...models.gpt_image2_edit_contract import GPT_IMAGE2_EDIT_CONTRACT_VERSION, GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION, GPT_IMAGE2_EDIT_CONTRACT_VERSIONS
from ...models.qwen20_image_contract import (
    QWEN20_IMAGE_EDIT_CONTRACT_VERSION,
    QWEN20_IMAGE_CONTRACT_VERSIONS, QWEN20_IMAGE_PROVIDER_MODELS,
)
from ...models.wan27_image_contract import (
    WAN27_IMAGE_EDIT_CONTRACT_VERSION, WAN27_IMAGE_CONTRACT_VERSIONS,
    WAN27_IMAGE_PROVIDER_MODELS,
)
from ...utils.generation_intent import RULES_VERSION
from ...utils.json_storage import StorageError
from ...utils.request_identity import REQUEST_IDENTITY_CONTRACT_VERSION, validate_client_request_id


STUDIO_ASSET_IMAGE_ENTRYPOINT = "studio.asset.reference_sheet.v1"
STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT = "studio.asset.scene_prop.v1"
STUDIO_FULL_BODY_IMAGE_ENTRYPOINT = "studio.asset.full_body.v1"
STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT = "studio.asset.three_view.v1"
STUDIO_HEADSHOT_IMAGE_ENTRYPOINT = "studio.asset.headshot.v1"
STUDIO_STORYBOARD_IMAGE_ENTRYPOINT = "studio.storyboard.image.v1"
STUDIO_GPT_IMAGE_MODEL = "gpt-image/gpt-image-2#image"
STUDIO_GPT_IMAGE_MODELS = frozenset({"gpt-image-2", STUDIO_GPT_IMAGE_MODEL})
STUDIO_WAN_IMAGE_MODELS = WAN27_IMAGE_PROVIDER_MODELS | {f"wan/{name}#image" for name in WAN27_IMAGE_PROVIDER_MODELS}
STUDIO_QWEN_IMAGE_MODELS = QWEN20_IMAGE_PROVIDER_MODELS | {f"qwen/{name}#image" for name in QWEN20_IMAGE_PROVIDER_MODELS}
_SAFE_ATTEMPT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _server_uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError("Studio image server identity must be a UUID string")
    try:
        canonical = str(UUID(value))
    except ValueError as exc:
        raise ValueError("Invalid Studio image server identity") from exc
    if canonical != value:
        raise ValueError("Studio image server identity must be a canonical UUID")
    return value


AssetImageServerId = Annotated[str, BeforeValidator(_server_uuid)]


def _business_id(value: object) -> str:
    if (
        type(value) is not str or not value or value.strip() != value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Invalid Studio image business identity")
    value.encode("utf-8")
    return value


def is_studio_gpt_image_execution(record: ProviderExecutionRecord) -> bool:
    if record.schema_version == 3 and record.handle is not None:
        return image_query_handle_from_record(record).query_contract is ProviderImageQueryContract.GPT_IMAGE2_GENERATION
    return (record.provider_model_id == "gpt-image-2"
            and record.intent.get("model_id") == STUDIO_GPT_IMAGE_MODEL
            and "intent_contract_version" not in record.intent)



def studio_image_material_complete(record: ProviderExecutionRecord) -> bool:
    """Distinguish actual omission from contradiction without reconstructing values."""
    if record.schema_version != 3:
        validate_sync_artifact_record_intent(record)
        return True
    if record.handle is None:
        validate_async_image_record_intent(record)
        return True
    handle = image_query_handle_from_record(record)
    contract = handle.query_contract
    edit = contract is ProviderImageQueryContract.GPT_IMAGE2_EDIT
    wan = contract is ProviderImageQueryContract.WAN27_IMAGE
    raw = record.intent
    wan_edit = wan and (raw.get("intent_contract_version") == WAN27_IMAGE_EDIT_CONTRACT_VERSION
                        or ("intent_contract_version" not in raw and raw.get("mode") == "i2i"))
    edit = edit or wan_edit
    expected = {"rules_version", "scope", "model_id", "catalog_mode", "mode", "prompt",
                "params", "sources", "request_fields", "preset_id", "preset_version"}
    if wan or edit:
        expected.add("intent_contract_version")
    if set(raw) - expected:
        raise ValueError("Unexpected historical image intent field")
    constants = {"rules_version": RULES_VERSION, "scope": "parameters-only", "catalog_mode": "image",
                 "mode": "i2i" if edit else "t2i",
                 "model_id": f"wan/{record.provider_model_id}#image" if wan else STUDIO_GPT_IMAGE_MODEL}
    if wan and "intent_contract_version" not in raw:
        constants.pop("mode")
        if "mode" in raw and (type(raw["mode"]) is not str or raw["mode"] not in {"t2i", "i2i"}):
            raise ValueError("Historical image material contradicts its handle")
    for key, value in constants.items():
        if key in raw and (type(raw[key]) is not str or raw[key] != value):
            raise ValueError("Historical image material contradicts its handle")
    if "intent_contract_version" in raw:
        versions = WAN27_IMAGE_CONTRACT_VERSIONS if wan else GPT_IMAGE2_EDIT_CONTRACT_VERSIONS
        if type(raw["intent_contract_version"]) is not str or raw["intent_contract_version"] not in versions:
            raise ValueError("Historical image contract contradicts its handle")
    if "prompt" in raw:
        prompt = raw["prompt"]
        if type(prompt) is not str or not prompt.strip() or (wan and len(prompt) > 5000):
            raise ValueError("Invalid historical image prompt")
        prompt.encode("utf-8")
    allowed_params = {"size", "watermark", "seed"} if wan else {"size"}
    required_params = {"size", "watermark"} if wan else {"size"}
    params = raw.get("params", {})
    if type(params) is not dict or set(params) - allowed_params:
        raise ValueError("Invalid historical image parameters")
    if "size" in params:
        size = params["size"]
        if wan:
            dimensions = re.fullmatch(r"([1-9][0-9]{0,4})\*([1-9][0-9]{0,4})", size) if type(size) is str else None
            if dimensions is None:
                raise ValueError("Invalid historical image size")
            width, height = map(int, dimensions.groups())
            edge = 4096 if record.provider_model_id == "wan2.7-image-pro" and not wan_edit else 2048
            if not 768**2 <= width * height <= edge**2 or width > 8 * height or height > 8 * width:
                raise ValueError("Invalid historical image size")
        elif type(size) is not str or size not in {"1024x1024", "1024x1536", "1536x1024"}:
            raise ValueError("Invalid historical GPT image size")
        elif edit and raw.get("intent_contract_version") == GPT_IMAGE2_EDIT_CONTRACT_VERSION and size != "1024x1024":
            raise ValueError("Historical v1 Edit size is not square")
    if "watermark" in params and type(params["watermark"]) is not bool:
        raise ValueError("Invalid historical watermark")
    if "seed" in params and (type(params["seed"]) is not int or not 0 <= params["seed"] <= 2147483647):
        raise ValueError("Invalid historical seed")
    fields = {"model_id", "mode", "prompt", *(f"params.{name}" for name in allowed_params)}
    sources = raw.get("sources", {})
    if type(sources) is not dict or any(key not in fields or type(value) is not str
            or value not in {"request", "preset", "catalog"} for key, value in sources.items()):
        raise ValueError("Invalid historical parameter provenance")
    supplied = raw.get("request_fields", [])
    if type(supplied) is not list or any(type(field) is not str or field not in fields for field in supplied) or len(supplied) != len(set(supplied)):
        raise ValueError("Invalid historical request fields")
    for key in ("preset_id", "preset_version"):
        if key in raw and raw[key] is not None:
            _business_id(raw[key])
    if not wan and not edit:
        supplied_fields = {"mode", "model_id", "prompt", "params.size"}
        if (("sources" in raw and sources != dict.fromkeys(supplied_fields, "request"))
                or ("request_fields" in raw and supplied != sorted(supplied_fields))
                or raw.get("preset_id") is not None or raw.get("preset_version") is not None):
            raise ValueError("Invalid historical GPT provenance")
    unknown_wan_mode = wan and "intent_contract_version" not in raw and "mode" not in raw
    if not edit and record.media and not unknown_wan_mode:
        raise ValueError("T2I history cannot contain references")
    if (edit or unknown_wan_mode) and len(record.media) > 1:
        raise ValueError("Edit history requires one original reference")
    for item in record.media:
        version = MediaVersion(**item)
        if version.role != "reference_image" or version.ordinal != 0 or version.declared_media_type not in {"image/png", "image/jpeg", "image/webp", "image/bmp"}:
            raise ValueError("Invalid historical Edit reference")
    complete = expected.issubset(raw) and required_params.issubset(params) and (not edit or len(record.media) == 1)
    if complete:
        validate_async_image_record_intent(record)
    return complete


def _same_present_material(left, right) -> bool:
    """Compare only present fields; absence never manufactures cross-member data."""
    if isinstance(left, dict) and isinstance(right, dict):
        return all(_same_present_material(left[key], right[key]) for key in left.keys() & right.keys())
    return left == right


def _async_family(record):
    if record.handle is not None:
        return image_query_handle_from_record(record).query_contract
    if record.intent.get("intent_contract_version") in WAN27_IMAGE_CONTRACT_VERSIONS:
        return ProviderImageQueryContract.WAN27_IMAGE
    if record.intent.get("intent_contract_version") in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
        return ProviderImageQueryContract.GPT_IMAGE2_EDIT
    return ProviderImageQueryContract.GPT_IMAGE2_GENERATION


def _validate_frozen_image(record: ProviderExecutionRecord) -> None:
    # Core owns the fingerprint, proof state and versioned provider contract.
    if record.schema_version == 3 and not studio_image_material_complete(record):
        return
    intent = record.intent
    gpt = is_studio_gpt_image_execution(record)
    edit = intent.get("intent_contract_version") in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, WAN27_IMAGE_EDIT_CONTRACT_VERSION, QWEN20_IMAGE_EDIT_CONTRACT_VERSION)
    if not gpt and intent.get("intent_contract_version") not in (*WAN27_IMAGE_CONTRACT_VERSIONS, *QWEN20_IMAGE_CONTRACT_VERSIONS, *GPT_IMAGE2_EDIT_CONTRACT_VERSIONS):
        raise ValueError("Studio image requires a supported bound image contract")
    try:
        if record.schema_version == 3:
            validate_async_image_record_intent(record)
            kind = "image"
        else:
            kind = validate_sync_artifact_record_intent(record)
    except (StorageError, TypeError, ValueError) as exc:
        raise ValueError("Invalid Studio image Core identity") from exc
    if kind != "image" or intent.get("mode") != ("i2i" if edit else "t2i") or (record.media and not edit):
        raise ValueError("Unsupported Studio image member")
    expected_intent = {
        "rules_version", "scope", "model_id", "catalog_mode", "mode", "prompt",
        "params", "sources", "request_fields", "preset_id", "preset_version",
    }
    if not gpt:
        expected_intent.add("intent_contract_version")
    if set(intent) != expected_intent or intent["rules_version"] != RULES_VERSION or intent["scope"] != "parameters-only":
        raise ValueError("Unsupported Studio image intent contract")
    _business_id(record.credential_ref)
    params = intent["params"]
    if gpt:
        scope = record.scope
        if (set(params) != {"size"} or params["size"] not in {"1024x1024", "1024x1536", "1536x1024"}
                or scope["provider"] != "mulerouter"
                or (scope["backend"], scope["gateway"]) not in {
                    ("mulerouter", "mulerouter_http"), ("mulerun_cli", "mulerun_token"), ("mulerun_cli", "mulerun_profile"),
                }
                or (scope["backend"] == "mulerouter" and scope["base_url"] != "https://api.mulerouter.ai")
                or any(scope.get(key) is not None for key in ("region", "workspace_id", "account_identity"))):
            raise ValueError("Unsupported Studio GPT image parameters or runtime")
        supplied = {"mode", "model_id", "prompt", "params.size"}
        if (intent["sources"] != dict.fromkeys(supplied, "request")
                or intent["request_fields"] != sorted(supplied)
                or intent["preset_id"] is not None or intent["preset_version"] is not None):
            raise ValueError("Invalid Studio GPT image provenance")
    fields = {"mode", "prompt", "model_id", *(f"params.{name}" for name in params)}
    if any(key not in fields or type(value) is not str or value not in {"request", "preset", "catalog"} for key, value in intent["sources"].items()):
        raise ValueError("Invalid Studio image parameter provenance")
    supplied = intent["request_fields"]
    if any(type(field) is not str or field not in fields for field in supplied) or len(supplied) != len(set(supplied)):
        raise ValueError("Invalid Studio image request fields")
    for name in ("preset_id", "preset_version"):
        if intent[name] is not None:
            _business_id(intent[name])


class AssetImageRequestMember(BaseModel):
    """One candidate identity with its sole Core execution record."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    variant_id: str
    execution: ProviderExecutionRecord

    @field_validator("execution", mode="before")
    @classmethod
    def _revalidate_execution(cls, value):
        # Revalidate mutable Core instances as well as JSON, including proof linkage.
        if isinstance(value, ProviderExecutionRecord):
            return ProviderExecutionRecord.model_validate(value.model_dump())
        return value

    @model_validator(mode="after")
    def _member_identity(self) -> AssetImageRequestMember:
        record = self.execution
        if _SAFE_ATTEMPT.fullmatch(record.attempt_id) is None or self.variant_id != record.attempt_id:
            raise ValueError("Studio image variant does not match a safe Core attempt")
        if record.output_ref != f"assets/studio/{record.attempt_id}.png":
            raise ValueError("Studio image output does not match its attempt")
        _validate_frozen_image(record)
        return self


class CharacterAllRequestState(BaseModel):
    """The two fixed derived receipts of an existing character all command."""

    model_config = ConfigDict(extra="forbid", strict=True)
    three_view_request_id: AssetImageServerId | None = None
    headshot_request_id: AssetImageServerId | None = None
    completed: bool = False

    @model_validator(mode="after")
    def _order(self):
        if (self.headshot_request_id is not None and self.three_view_request_id is None
                or self.completed and self.headshot_request_id is None
                or self.headshot_request_id is not None and self.headshot_request_id == self.three_view_request_id):
            raise ValueError("Invalid character all stage order")
        return self


class AssetImageRequestRecord(BaseModel):
    """Frozen request identity and ordered members stored only at the actual owner."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    schema_version: Literal[1]
    id: AssetImageServerId
    created_at: float = Field(ge=0, allow_inf_nan=False)
    request_contract_version: str
    entrypoint: Literal["studio.asset.reference_sheet.v1", "studio.asset.scene_prop.v1", "studio.asset.full_body.v1", "studio.asset.three_view.v1", "studio.asset.headshot.v1", "studio.storyboard.image.v1"]
    client_request_id: str
    request_fingerprint: str
    request_root_kind: Literal["project", "series"]
    request_root_id: str
    request_root_scope_id: AssetImageServerId
    request_root_series_id: str | None
    owner_kind: Literal["script", "series", "global"]
    owner_id: str
    asset_type: Literal["character", "scene", "prop", "storyboard_frame"]
    asset_id: str
    channel: Literal["reference_sheet", "image", "full_body", "three_view", "headshot"]
    base_prompt: str | None = None
    character_all: CharacterAllRequestState | None = None
    input_guard: str
    projection_guard: str
    members: list[AssetImageRequestMember] = Field(min_length=1)

    @model_serializer(mode="wrap")
    def _serialize_request(self, handler):
        data = handler(self)
        if self.base_prompt is None:
            data.pop("base_prompt", None)
        if self.character_all is None:
            data.pop("character_all", None)
        return data

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported Studio image request schema")
        return value

    @field_validator("request_contract_version")
    @classmethod
    def _request_version(cls, value: str) -> str:
        if value != REQUEST_IDENTITY_CONTRACT_VERSION:
            raise ValueError("Unsupported Studio image request identity contract")
        return value

    @field_validator("client_request_id", mode="before")
    @classmethod
    def _client_key(cls, value):
        return validate_client_request_id(value)

    @field_validator("request_root_id", "owner_id", "asset_id", "request_root_series_id", mode="before")
    @classmethod
    def _business_identity(cls, value, info):
        if info.field_name == "request_root_series_id" and value is None:
            return None
        return _business_id(value)

    @field_validator("request_fingerprint", "input_guard", "projection_guard")
    @classmethod
    def _hash(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Invalid Studio image fingerprint")
        return value

    @model_validator(mode="after")
    def _internal_relationships(self) -> AssetImageRequestRecord:
        if (self.entrypoint, self.asset_type, self.channel) not in {
            (STUDIO_ASSET_IMAGE_ENTRYPOINT, "character", "reference_sheet"),
            (STUDIO_FULL_BODY_IMAGE_ENTRYPOINT, "character", "full_body"),
            (STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, "character", "three_view"),
            (STUDIO_HEADSHOT_IMAGE_ENTRYPOINT, "character", "headshot"),
            (STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT, "scene", "image"),
            (STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT, "prop", "image"),
            (STUDIO_STORYBOARD_IMAGE_ENTRYPOINT, "storyboard_frame", "image"),
        }:
            raise ValueError("Unsupported Studio image entrypoint, asset type or channel")
        if self.character_all is not None and (
                self.asset_type != "character" or self.channel != "full_body" or len(self.members) != 1
                or self.id in {self.character_all.three_view_request_id, self.character_all.headshot_request_id}):
            raise ValueError("Character all must belong to its single full-body request")
        asynchronous = any(member.execution.schema_version == 3 for member in self.members)
        if asynchronous:
            first = self.members[0].execution
            family = _async_family(first)
            fields = {"provider_model_id", "scope", "credential_ref"}
            for member in self.members:
                execution = member.execution
                if (execution.schema_version != 3 or _async_family(execution) is not family
                        or execution.model_dump(include=fields) != first.model_dump(include=fields)
                        or not _same_present_material(execution.intent, first.intent)
                        or (execution.media and first.media and execution.media != first.media)):
                    raise ValueError("Inconsistent async image batch")
            # Pairwise comparison catches contradictions hidden by an empty first member.
            for index, member in enumerate(self.members):
                for previous in self.members[:index]:
                    if (not _same_present_material(member.execution.intent, previous.execution.intent)
                            or (member.execution.media and previous.execution.media
                                and member.execution.media != previous.execution.media)):
                        raise ValueError("Conflicting async image batch material")
            wan_edit = family is ProviderImageQueryContract.WAN27_IMAGE and (
                self.channel == "three_view" or any(
                    member.execution.intent.get("intent_contract_version") == WAN27_IMAGE_EDIT_CONTRACT_VERSION
                    or member.execution.intent.get("mode") == "i2i" for member in self.members))
            if wan_edit:
                if (self.request_root_kind != "project" or self.entrypoint != STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT
                        or any("intent_contract_version" in member.execution.intent
                               and member.execution.intent["intent_contract_version"] != WAN27_IMAGE_EDIT_CONTRACT_VERSION
                               for member in self.members)):
                    raise ValueError("Unsupported async Studio Wan Edit tuple")
            elif family is ProviderImageQueryContract.GPT_IMAGE2_EDIT:
                expected_version = (GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION if self.channel == "full_body"
                                    else GPT_IMAGE2_EDIT_CONTRACT_VERSION)
                if (self.entrypoint not in {
                        STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, STUDIO_HEADSHOT_IMAGE_ENTRYPOINT, STUDIO_FULL_BODY_IMAGE_ENTRYPOINT}
                        or any("intent_contract_version" in member.execution.intent
                               and member.execution.intent["intent_contract_version"] != expected_version
                               for member in self.members)):
                    raise ValueError("Unsupported async Studio Edit tuple")
            elif self.entrypoint not in {STUDIO_ASSET_IMAGE_ENTRYPOINT, STUDIO_FULL_BODY_IMAGE_ENTRYPOINT, STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT}:
                raise ValueError("Unsupported async Studio T2I tuple")
        if not asynchronous and any(is_studio_gpt_image_execution(member.execution) for member in self.members):
            first = self.members[0].execution
            fields = {"provider_model_id", "intent", "scope", "credential_ref"}
            if (self.entrypoint not in {
                    STUDIO_ASSET_IMAGE_ENTRYPOINT, STUDIO_FULL_BODY_IMAGE_ENTRYPOINT, STUDIO_SCENE_PROP_IMAGE_ENTRYPOINT,
                    }
                    or any(not is_studio_gpt_image_execution(member.execution)
                           or member.execution.model_dump(include=fields) != first.model_dump(include=fields)
                           for member in self.members)):
                raise ValueError("Unsupported Studio GPT image request tuple or members")
        if not asynchronous and (self.entrypoint in {STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, STUDIO_HEADSHOT_IMAGE_ENTRYPOINT} or any(
            member.execution.intent.get("intent_contract_version") in (*GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, QWEN20_IMAGE_EDIT_CONTRACT_VERSION)
            for member in self.members
        )):
            first = self.members[0].execution
            fields = {"provider_model_id", "intent", "scope", "credential_ref", "media"}
            qwen_edit = first.intent.get("intent_contract_version") == QWEN20_IMAGE_EDIT_CONTRACT_VERSION
            version = (QWEN20_IMAGE_EDIT_CONTRACT_VERSION if qwen_edit else
                       GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION if self.entrypoint == STUDIO_FULL_BODY_IMAGE_ENTRYPOINT
                       else GPT_IMAGE2_EDIT_CONTRACT_VERSION)
            if ((qwen_edit and (self.entrypoint not in {STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, STUDIO_STORYBOARD_IMAGE_ENTRYPOINT}
                               or self.request_root_kind != "project"))
                    or self.entrypoint not in {STUDIO_THREE_VIEW_IMAGE_ENTRYPOINT, STUDIO_HEADSHOT_IMAGE_ENTRYPOINT,
                                              STUDIO_FULL_BODY_IMAGE_ENTRYPOINT, STUDIO_STORYBOARD_IMAGE_ENTRYPOINT}
                    or any(member.execution.intent.get("intent_contract_version") != version
                           or member.execution.model_dump(include=fields) != first.model_dump(include=fields)
                           for member in self.members)):
                label = {STUDIO_HEADSHOT_IMAGE_ENTRYPOINT: "headshot", STUDIO_FULL_BODY_IMAGE_ENTRYPOINT: "full-body"}.get(self.entrypoint, "three-view")
                raise ValueError(f"Unsupported Studio {label} request tuple or members")
        if self.asset_type == "storyboard_frame":
            if (self.request_root_kind != "project" or self.owner_kind != "script"
                    or self.owner_id != self.request_root_id or self.base_prompt is None
                    or len(self.members) != 1
                    or self.members[0].execution.intent.get("intent_contract_version") not in QWEN20_IMAGE_CONTRACT_VERSIONS):
                raise ValueError("Invalid Studio storyboard image history")
            params = self.members[0].execution.intent["params"]
            if (set(params) != {"size", "watermark", "prompt_extend"}
                    or params["watermark"] is not False or params["prompt_extend"] is not True):
                raise ValueError("Invalid Studio storyboard image parameters")
        elif self.channel == "full_body":
            if not self.base_prompt or not self.base_prompt.strip():
                raise ValueError("Full body history requires its frozen base prompt")
        elif self.channel == "three_view":
            if not self.base_prompt or not self.base_prompt.strip():
                raise ValueError("Three view history requires its frozen base prompt")
        elif self.channel == "headshot":
            if not self.base_prompt or not self.base_prompt.strip():
                raise ValueError("Headshot history requires its frozen base prompt")
        elif self.base_prompt is not None:
            raise ValueError("This image history cannot carry a frozen base prompt")
        if self.owner_kind == "global" and self.owner_id != "library":
            raise ValueError("Invalid Studio image global owner")
        if self.request_root_kind == "series":
            if self.owner_kind != "series" or self.owner_id != self.request_root_id or self.request_root_series_id is not None:
                raise ValueError("Invalid Studio image standalone series owner")
        elif self.owner_kind == "script" and self.owner_id != self.request_root_id:
            raise ValueError("Studio image script owner differs from request root")
        elif self.owner_kind == "series" and self.owner_id != self.request_root_series_id:
            raise ValueError("Studio image series owner differs from original project relation")
        attempts = [member.execution.attempt_id for member in self.members]
        if len(attempts) != len(set(attempts)):
            raise ValueError("Duplicate Studio image request member")
        return self


def validate_asset_image_requests(
    requests: list[AssetImageRequestRecord], *, owner_kind: str, owner_id: str,
) -> None:
    """Validate owner-local history, deliberately without resolving live targets."""
    ids: set[str] = set()
    keys: set[tuple[str, str, str]] = set()
    attempts: set[str] = set()
    for request in requests:
        if (request.owner_kind, request.owner_id) != (owner_kind, owner_id):
            raise ValueError("Studio image request is stored at a foreign owner")
        key = (request.request_root_kind, request.request_root_id, request.client_request_id)
        if request.id in ids or key in keys:
            raise ValueError("Duplicate Studio image request identity")
        ids.add(request.id)
        keys.add(key)
        for member in request.members:
            if member.execution.attempt_id in attempts:
                raise ValueError("Duplicate Studio image member across requests")
            attempts.add(member.execution.attempt_id)
