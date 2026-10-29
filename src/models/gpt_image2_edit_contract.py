"""Exact opt-in GPT Image 2 Edit values, without live routing or provider I/O."""

from collections.abc import Mapping, Sequence

from ..utils.generation_intent import GenerationIntent, RULES_VERSION
from .provider_tasks import ProviderBindingError, ProviderScope


GPT_IMAGE2_EDIT_CONTRACT_VERSION = "provider-gpt-image2-edit-bound-v1"
GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION = "provider-gpt-image2-edit-bound-v2"
GPT_IMAGE2_EDIT_CONTRACT_VERSIONS = (
    GPT_IMAGE2_EDIT_CONTRACT_VERSION, GPT_IMAGE2_EDIT_SIZES_CONTRACT_VERSION,
)
GPT_IMAGE2_EDIT_PROVIDER_MODEL = "gpt-image-2"
GPT_IMAGE2_EDIT_MODEL_ID = "gpt-image/gpt-image-2#image"
GPT_IMAGE2_EDIT_SIZE = "1024x1024"
GPT_IMAGE2_EDIT_SIZES = (GPT_IMAGE2_EDIT_SIZE, "1536x1024", "1024x1536")


def validate_gpt_image2_edit_parameters(
    prompt: str, params: Mapping[str, object], *,
    contract_version: object = GPT_IMAGE2_EDIT_CONTRACT_VERSION,
) -> None:
    """Reject unsupported presence before defaults, coercion or credential binding."""
    if type(contract_version) is not str or contract_version not in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS:
        raise ProviderBindingError("Unsupported bound GPT Edit contract")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderBindingError("Invalid bound GPT Edit prompt")
    try:
        prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise ProviderBindingError("Invalid bound GPT Edit prompt encoding") from None
    # Persisted v1 receipts keep their original square-only semantics.
    sizes = (GPT_IMAGE2_EDIT_SIZE,) if contract_version == GPT_IMAGE2_EDIT_CONTRACT_VERSION else GPT_IMAGE2_EDIT_SIZES
    if (
        not isinstance(params, Mapping)
        or set(params) != {"size"}
        or type(params["size"]) is not str
        or params["size"] not in sizes
    ):
        raise ProviderBindingError("Bound GPT Edit supports only size=" + ",".join(sizes))


def validate_gpt_image2_edit_scope(scope: ProviderScope) -> None:
    """Keep the three existing Mule authorities; do not infer a new endpoint."""
    if (
        not isinstance(scope, ProviderScope)
        or scope.provider != "mulerouter"
        or (scope.backend, scope.gateway) not in {
            ("mulerouter", "mulerouter_http"),
            ("mulerun_cli", "mulerun_token"),
            ("mulerun_cli", "mulerun_profile"),
        }
        or scope.region is not None
        or scope.workspace_id is not None
        or scope.account_identity is not None
        or (scope.backend == "mulerouter" and scope.base_url != "https://api.mulerouter.ai")
    ):
        raise ProviderBindingError("Unsupported bound GPT Edit scope")


def validate_gpt_image2_edit_intent(
    intent: GenerationIntent, provider_model_id: str, scope: ProviderScope,
    *, contract_version: object = GPT_IMAGE2_EDIT_CONTRACT_VERSION,
) -> None:
    """Validate frozen inputs without adding catalog defaults or changing identity."""
    if (
        not isinstance(intent, GenerationIntent)
        or provider_model_id != GPT_IMAGE2_EDIT_PROVIDER_MODEL
        or intent.model_id != GPT_IMAGE2_EDIT_MODEL_ID
        or intent.catalog_mode != "image"
        or intent.mode != "i2i"
        or intent.rules_version != RULES_VERSION
    ):
        raise ProviderBindingError("Unsupported bound GPT Edit intent")
    validate_gpt_image2_edit_parameters(intent.prompt, intent.params, contract_version=contract_version)
    fields = {"model_id", "mode", "prompt", "params.size"}
    if not isinstance(intent.sources, Mapping) or any(
        type(key) is not str or key not in fields
        or type(value) is not str or value not in {"request", "preset", "catalog"}
        for key, value in intent.sources.items()
    ):
        raise ProviderBindingError("Invalid bound GPT Edit parameter provenance")
    if (
        not isinstance(intent.request_fields, (tuple, list))
        or any(type(field) is not str or field not in fields for field in intent.request_fields)
        or len(intent.request_fields) != len(set(intent.request_fields))
    ):
        raise ProviderBindingError("Invalid bound GPT Edit request fields")
    for value in (intent.preset_id, intent.preset_version):
        if value is not None:
            if (
                type(value) is not str or not value or value.strip() != value
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ProviderBindingError("Invalid bound GPT Edit preset identity")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ProviderBindingError("Invalid bound GPT Edit preset identity") from None
    validate_gpt_image2_edit_scope(scope)


def gpt_image2_edit_payload(
    prompt: str, size: str, images: Sequence[str], *,
    contract_version: object = GPT_IMAGE2_EDIT_CONTRACT_VERSION,
) -> dict:
    """Build the restricted official Edit body; quality belongs to generation."""
    validate_gpt_image2_edit_parameters(prompt, {"size": size}, contract_version=contract_version)
    if (
        not isinstance(images, (tuple, list)) or len(images) != 1
        or not isinstance(images[0], str) or not images[0]
    ):
        raise ProviderBindingError("Bound GPT Edit requires one resolved image")
    return {"prompt": prompt, "size": size, "n": 1, "images": list(images)}
