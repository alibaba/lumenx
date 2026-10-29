import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse


LOCAL_MEDIA_PREFIXES = (
    "assets/",
    "storyboard/",
    "video/",
    "audio/",
    "export/",
    "uploads/",
    "playground/",
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


def _output_root(
    project_root: Optional[str] = None,
    output_root: Optional[str] = None,
) -> Path:
    if output_root:
        return Path(output_root).expanduser().resolve()
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
    output_root: Optional[str] = None,
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

    resolved_output_root = _output_root(project_root, output_root)
    if os.path.isabs(raw):
        return MEDIA_REF_LOCAL_PATH if _is_under(Path(raw), resolved_output_root) else MEDIA_REF_UNKNOWN

    relative = raw.lstrip("/")
    if relative.startswith(LOCAL_MEDIA_PREFIXES):
        return MEDIA_REF_LOCAL_PATH

    base_path = _normalized_oss_base_path(oss_base_path)
    if base_path and relative.startswith(f"{base_path}/"):
        return MEDIA_REF_OBJECT_KEY

    return MEDIA_REF_UNKNOWN


def resolve_local_media_path(
    value: str,
    *,
    project_root: Optional[str] = None,
    output_root: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a local media reference to an absolute filesystem path under output/.
    Returns None when the input is not a local media reference.
    """
    if classify_media_ref(value, project_root=project_root, output_root=output_root) != MEDIA_REF_LOCAL_PATH:
        return None

    raw = value.strip()
    resolved_output_root = os.path.realpath(str(_output_root(project_root, output_root)))

    if os.path.isabs(raw):
        abs_path = os.path.realpath(raw)
        if abs_path == resolved_output_root or abs_path.startswith(resolved_output_root + os.sep):
            return abs_path
        return None

    relative = raw.lstrip("/")
    if relative.startswith("output/"):
        relative = relative[len("output/") :]
    elif relative.startswith("outputs/"):
        relative = relative[len("outputs/") :]

    abs_path = os.path.realpath(os.path.join(resolved_output_root, relative))
    if abs_path == resolved_output_root or abs_path.startswith(resolved_output_root + os.sep):
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


def payload_contains_managed_ref(value: Any, target_ref: str, output_root: str | Path) -> bool:
    target_path = _managed_ref_realpath(target_ref, output_root)
    if target_path is None:
        return False
    if isinstance(value, str):
        candidate_path = _managed_ref_realpath(value, output_root)
        return candidate_path == target_path
    if isinstance(value, dict):
        return any(payload_contains_managed_ref(item, target_ref, output_root) for item in value.values())
    if isinstance(value, list | tuple):
        return any(payload_contains_managed_ref(item, target_ref, output_root) for item in value)
    return False


def _managed_ref_realpath(value: str, output_root: str | Path) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.path:
        raw = _files_ref_to_media_ref(unquote(parsed.path)) or raw
    else:
        raw = _files_ref_to_media_ref(raw) or raw
    resolved = resolve_local_media_path(raw, output_root=str(output_root))
    if resolved is None:
        return None
    return str(Path(resolved).resolve())


def _files_ref_to_media_ref(path: str) -> str | None:
    value = path.strip()
    if value.startswith("/") and value.lstrip("/").startswith(LOCAL_MEDIA_PREFIXES):
        return value.lstrip("/")
    mappings = (
        ("/files/outputs/videos/", "video/"),
        ("files/outputs/videos/", "video/"),
        ("/files/outputs/assets/", "assets/"),
        ("files/outputs/assets/", "assets/"),
        ("/files/videos/", "video/"),
        ("files/videos/", "video/"),
        ("/files/video/", "video/"),
        ("files/video/", "video/"),
        ("/files/assets/", "assets/"),
        ("files/assets/", "assets/"),
        ("/files/outputs/", ""),
        ("files/outputs/", ""),
        ("/files/output/", ""),
        ("files/output/", ""),
        ("/files/", ""),
        ("files/", ""),
    )
    for prefix, replacement in mappings:
        if value.startswith(prefix):
            return replacement + value[len(prefix):].lstrip("/")
    return None
