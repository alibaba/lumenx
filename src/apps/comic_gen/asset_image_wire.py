"""Public Studio image receipt projection without provider or execution authority."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .asset_image_models import AssetImageRequestRecord
from ...utils.json_storage import StorageError

if TYPE_CHECKING:
    from .asset_image_execution import AssetImageAdmission

_REQUEST_FIELDS = {
    "id", "entrypoint", "request_contract_version", "client_request_id", "request_fingerprint",
    "request_root_kind", "request_root_id", "request_root_scope_id", "request_root_series_id",
    "owner_kind", "owner_id", "asset_type", "asset_id", "channel",
}
_STATUSES = {"pending", "processing", "needs_confirmation", "completed", "cancelled"}


def _reason(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise StorageError("Invalid Studio image request state")
    return value


def asset_image_request_wire(
    result: "AssetImageAdmission", state: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose ordered public identity and only a confirmed canonical output ref."""
    try:
        record = AssetImageRequestRecord.model_validate(result.request.model_dump())
    except (TypeError, ValueError) as exc:
        raise StorageError("Invalid retained Studio image request") from exc
    if type(result.replayed) is not bool or not isinstance(state, Mapping):
        raise StorageError("Invalid Studio image request state")
    states = state.get("members")
    if not isinstance(states, list) or len(states) != len(record.members):
        raise StorageError("Invalid Studio image request members")
    members = []
    for member, current in zip(record.members, states, strict=True):
        if not isinstance(current, Mapping) or current.get("variant_id") != member.variant_id:
            raise StorageError("Studio image request member identity changed")
        status = current.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise StorageError("Invalid Studio image request member status")
        public = {
            "variant_id": member.variant_id,
            "attempt_id": member.execution.attempt_id,
            "status": status,
            "reason": _reason(current.get("reason")),
        }
        if status == "completed":
            if current.get("output_ref") != member.execution.output_ref:
                raise StorageError("Invalid Studio image completed output")
            public["output_ref"] = member.execution.output_ref
        members.append(public)
    request = record.model_dump(mode="json", include=_REQUEST_FIELDS)
    request["members"] = members
    return {
        "schema_version": 1,
        "contract_version": "studio-asset-image-request-v1",
        "replayed": result.replayed,
        "reason": _reason(result.reason),
        "request": request,
    }
