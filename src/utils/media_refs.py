import os
from pathlib import Path
from typing import Optional


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
    return _project_root(project_root) / "output"


def _is_under(path: Path, parent: Path) -> bool:
    resolved = os.path.realpath(str(path))
    parent_real = os.path.realpath(str(parent))
    return resolved == parent_real or resolved.startswith(parent_real + os.sep)


def _normalized_oss_base_path(oss_base_path: Optional[str] = None) -> str:
    value = oss_base_path if oss_base_path is not None else os.getenv("OSS_BASE_PATH", "lumenx")
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

    if raw.startswith("data:"):
        return MEDIA_REF_DATA_URI

    if raw.startswith("blob:"):
        return MEDIA_REF_BLOB_URL

    if raw.startswith(("http://", "https://")):
        return MEDIA_REF_REMOTE_URL

    output_root = _output_root(project_root)
    if os.path.isabs(raw):
        return MEDIA_REF_LOCAL_PATH if _is_under(Path(raw), output_root) else MEDIA_REF_UNKNOWN

    relative = raw.lstrip("/")
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
    output_root = os.path.realpath(str(_output_root(project_root)))

    if os.path.isabs(raw):
        abs_path = os.path.realpath(raw)
        if abs_path.startswith(output_root + os.sep):
            return abs_path
        return None

    relative = raw.lstrip("/")
    if relative.startswith("output/"):
        relative = relative[len("output/") :]
    elif relative.startswith("outputs/"):
        relative = relative[len("outputs/") :]

    abs_path = os.path.realpath(os.path.join(output_root, relative))
    if abs_path.startswith(output_root + os.sep):
        return abs_path
    return None


def is_remote_media_ref(value: str) -> bool:
    return classify_media_ref(value) in {MEDIA_REF_REMOTE_URL, MEDIA_REF_BLOB_URL}


def is_stable_project_media_ref(value: str) -> bool:
    return classify_media_ref(value) in {
        MEDIA_REF_LOCAL_PATH,
        MEDIA_REF_OBJECT_KEY,
        MEDIA_REF_REMOTE_URL,
    }
