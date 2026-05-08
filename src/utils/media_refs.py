import os
from pathlib import Path
from typing import Optional

from .runtime_config import get_output_root


LOCAL_MEDIA_PREFIXES = (
    "assets/",
    "storyboard/",
    "video/",
    "audio/",
    "export/",
    "uploads/",
    "output/",
    "outputs/",
)

MEDIA_REF_LOCAL_PATH = "local_path"
MEDIA_REF_OBJECT_KEY = "object_key"
MEDIA_REF_REMOTE_URL = "remote_url"
MEDIA_REF_BLOB_URL = "blob_url"
MEDIA_REF_DATA_URI = "data_uri"
MEDIA_REF_UNKNOWN = "unknown"


def _project_root(project_root: Optional[str] = None) -> Path:
    if project_root:
        return Path(project_root).resolve()
    # src/utils/media_refs.py -> repo root
    return Path(__file__).resolve().parents[2]


def _output_root(project_root: Optional[str] = None) -> Path:
    return get_output_root(project_root=project_root)


def normalize_project_media_ref(value: str) -> str:
    if not isinstance(value, str):
        return value
    return value.strip().replace("\\", "/")


def output_media_ref(path: str, *, project_root: Optional[str] = None) -> str:
    output_root = _output_root(project_root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (_project_root(project_root) / candidate).resolve()
    else:
        candidate = candidate.resolve()
    rel_path = os.path.relpath(str(candidate), str(output_root))
    return normalize_project_media_ref(rel_path)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _normalized_oss_base_path(oss_base_path: Optional[str] = None) -> str:
    if oss_base_path is not None:
        value = oss_base_path
    else:
        value = (
            os.getenv("OSS_BASE_PATH")
            or os.getenv("TOS_BASE_PATH")
            or os.getenv("OBJECT_STORAGE_BASE_PATH")
            or "lumenx"
        )
    return str(value).strip().strip("'\"/ ")


def classify_media_ref(
    value: str,
    *,
    oss_base_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> str:
    """Classify media reference string used in project state."""
    if not isinstance(value, str):
        return MEDIA_REF_UNKNOWN

    raw = value.strip()
    if not raw:
        return MEDIA_REF_UNKNOWN

    normalized = normalize_project_media_ref(raw)

    if normalized.startswith("data:"):
        return MEDIA_REF_DATA_URI

    if normalized.startswith("blob:"):
        return MEDIA_REF_BLOB_URL

    if normalized.startswith(("http://", "https://")):
        return MEDIA_REF_REMOTE_URL

    output_root = _output_root(project_root)
    if os.path.isabs(raw):
        return MEDIA_REF_LOCAL_PATH if _is_under(Path(raw), output_root) else MEDIA_REF_UNKNOWN

    relative = normalized.lstrip("/")
    if relative.startswith(LOCAL_MEDIA_PREFIXES):
        return MEDIA_REF_LOCAL_PATH

    base_path = _normalized_oss_base_path(oss_base_path)
    if base_path and relative.startswith(f"{base_path}/"):
        return MEDIA_REF_OBJECT_KEY

    return MEDIA_REF_UNKNOWN


def resolve_local_media_path(value: str, *, project_root: Optional[str] = None) -> Optional[str]:
    """
    Resolve a local media reference to an absolute filesystem path under output/.
    Returns None when the input is not a local media reference.
    """
    if classify_media_ref(value, project_root=project_root) != MEDIA_REF_LOCAL_PATH:
        return None

    raw = value.strip()
    output_root = _output_root(project_root).resolve()

    if os.path.isabs(raw):
        abs_path = Path(raw).resolve()
        return str(abs_path) if _is_under(abs_path, output_root) else None

    relative = normalize_project_media_ref(raw).lstrip("/")
    if relative.startswith("output/"):
        relative = relative[len("output/") :]
    elif relative.startswith("outputs/"):
        relative = relative[len("outputs/") :]

    abs_path = (output_root / relative).resolve()
    return str(abs_path) if _is_under(abs_path, output_root) else None


def is_remote_media_ref(value: str) -> bool:
    return classify_media_ref(value) in {MEDIA_REF_REMOTE_URL, MEDIA_REF_BLOB_URL}


def is_stable_project_media_ref(value: str) -> bool:
    return classify_media_ref(value) in {
        MEDIA_REF_LOCAL_PATH,
        MEDIA_REF_OBJECT_KEY,
        MEDIA_REF_REMOTE_URL,
    }
