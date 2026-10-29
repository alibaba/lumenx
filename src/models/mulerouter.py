"""MuleRouter/MuleRun provider adapter for Seedance 2.0 (video) and GPT-Image-2 (image).

Supports two backends:
- MuleRun CLI mode: uses `mulerun studio run` subprocess (auth via MULERUN_TOKEN or browser login)
- MuleRouter HTTP mode: direct API calls to api.mulerouter.ai (auth via MULEROUTER_API_KEY)

Priority: MULERUN_TOKEN / CLI available → CLI mode; else → HTTP API mode.
"""

import base64
from contextlib import nullcontext
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import time
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import requests

from .base import VideoGenModel
from . import provider_tasks as _image_tasks
from .bound_image_http import (
    ImageReceiptWriter, request_json_envelope, validate_receipt_writer, write_submitted_handle,
)
from .image import ImageGenModel
from .provider_binding import ResolvedMuleRunProfileCredential, ResolvedMuleRunTokenCredential
from .gpt_image2_edit_contract import (
    GPT_IMAGE2_EDIT_CONTRACT_VERSIONS, gpt_image2_edit_payload,
    validate_gpt_image2_edit_parameters, validate_gpt_image2_edit_scope,
)
from ..utils.media_refs import resolve_local_media_path
from ..utils.mulerun_profile import (
    MuleRunLoginSession,
    MuleRunProfileError,
    build_mulerun_profile_env,
    fingerprint_owned_mulerun_profile,
    mulerun_global_flags,
    mulerun_profile_home,
    snapshot_mulerun_profile_to_temp_home,
)

logger = logging.getLogger(__name__)

SEEDANCE_API_PATHS = {
    "t2v": "/vendors/bytedance/v1/seedance-2.0/text-to-video/generation",
    "t2v-fast": "/vendors/bytedance/v1/seedance-2.0-fast/text-to-video/generation",
    "i2v": "/vendors/bytedance/v1/seedance-2.0/image-to-video/generation",
    "i2v-fast": "/vendors/bytedance/v1/seedance-2.0-fast/image-to-video/generation",
    "r2v": "/vendors/bytedance/v1/seedance-2.0/reference-to-video/generation",
    "r2v-fast": "/vendors/bytedance/v1/seedance-2.0-fast/reference-to-video/generation",
}

GPT_IMAGE_API_PATHS = {
    "generation": "/vendors/openai/v1/gpt-image-2/generation",
    "edit": "/vendors/openai/v1/gpt-image-2/edit",
}

POLL_INTERVAL = 20
MAX_WAIT = 900

GPT_IMAGE_VALID_SIZES = {
    "1024x1024", "1536x1024", "1024x1536",
    "2048x2048", "2048x1152", "3840x2160", "2160x3840", "auto",
}


def _normalize_gpt_image_size(size: str) -> str:
    """Convert DashScope-style size (e.g. 1024*768) to GPT-Image-2 format."""
    normalized = size.replace("*", "x")
    if normalized in GPT_IMAGE_VALID_SIZES:
        return normalized
    try:
        w, h = [int(d) for d in normalized.split("x")]
    except (ValueError, AttributeError):
        return "1024x1024"
    if w > h:
        return "1536x1024"
    elif h > w:
        return "1024x1536"
    return "1024x1024"

SITE_BASE_URLS = {
    "mulerouter": "https://api.mulerouter.ai",
    "mulerun": "https://api.mulerun.com",
}

# gpt-image-2 is only available on the mulerouter site
MULEROUTER_ONLY_MODELS = ("openai/gpt-image-2",)

# ---------------------------------------------------------------------------
# Site resolution: mulerouter vs mulerun
# ---------------------------------------------------------------------------

def _get_site(env: Mapping[str, str] | None = None) -> str:
    """Get the configured site. Default: mulerouter (more models available)."""
    environment = os.environ if env is None else env
    return (environment.get("MULEROUTER_SITE") or "mulerouter").strip().lower()


def _get_base_url_for_model(model_prefix: str, env: Mapping[str, str] | None = None) -> str:
    """Route to the correct site based on model availability."""
    for prefix in MULEROUTER_ONLY_MODELS:
        if model_prefix.startswith(prefix):
            return SITE_BASE_URLS["mulerouter"]
    environment = os.environ if env is None else env
    site = _get_site(env)
    custom = environment.get("MULEROUTER_BASE_URL")
    if custom:
        return custom.rstrip("/")
    return SITE_BASE_URLS.get(site, SITE_BASE_URLS["mulerouter"])


# ---------------------------------------------------------------------------
# Backend detection: MuleRun CLI vs MuleRouter HTTP API
# ---------------------------------------------------------------------------

def _is_mulerun_cli_available(env: Mapping[str, str] | None = None) -> bool:
    """Check if MuleRun CLI is usable without falling back to ambient profiles."""
    if shutil.which("mulerun") is None:
        return False
    if env is not None:
        if env.get("MULERUN_TOKEN"):
            return True
        profile_home = mulerun_profile_home(env)
        if profile_home is None:
            return False
        return MuleRunLoginSession(profile_home, env).status()
    try:
        result = subprocess.run(
            ["mulerun", "login", "status"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


_cli_available_cache: Optional[bool] = None


def _use_cli_backend(env: Mapping[str, str] | None = None) -> bool:
    """Determine whether to use CLI or HTTP API backend.

    Priority: MULEROUTER_API_KEY (HTTP) > CLI fallback.
    CLI is used only when no API key is configured and mulerun is logged in.
    """
    environment = os.environ if env is None else env
    if environment.get("MULEROUTER_API_KEY"):
        return False
    if env is not None:
        if not environment.get("MULERUN_TOKEN") and mulerun_profile_home(environment) is None:
            return False
        return _is_mulerun_cli_available(env)

    global _cli_available_cache
    if _cli_available_cache is None:
        _cli_available_cache = _is_mulerun_cli_available()
    return _cli_available_cache


# ---------------------------------------------------------------------------
# MuleRun CLI execution layer
# ---------------------------------------------------------------------------

_SUBPROCESS_ENV_ALLOWLIST = (
    "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SHELL", "USER", "LOGNAME",
    "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
)


def _build_mulerun_subprocess_env(
    env: Mapping[str, str] | None = None,
    *,
    profile_root: str | os.PathLike[str] | None = None,
    create_dirs: bool = True,
) -> Dict[str, str]:
    if env is None:
        child_env = os.environ.copy()
        child_env["MULERUN_DISABLE_AUTO_UPGRADE"] = "1"
        child_env["MULERUN_SKIP_AUTO_UPGRADE"] = "1"
        child_env["NO_UPDATE_NOTIFIER"] = "1"
        token = os.getenv("MULERUN_TOKEN")
        if token:
            child_env["MULERUN_TOKEN"] = token
        return child_env

    if profile_root is None:
        raise RuntimeError("An app-owned MuleRun CLI HOME is required")
    try:
        return build_mulerun_profile_env(
            env,
            profile_home=profile_root,
            token_required=bool(env.get("MULERUN_TOKEN")),
            include_token=bool(env.get("MULERUN_TOKEN")),
            create_dirs=create_dirs,
        )
    except MuleRunProfileError as exc:
        raise RuntimeError(str(exc)) from exc


def _mulerun_control_flags(
    env: Mapping[str, str] | None = None,
    *,
    authority: ResolvedMuleRunTokenCredential | ResolvedMuleRunProfileCredential | None = None,
) -> Tuple[List[str], List[str]]:
    if authority is not None:
        routes = authority.routes
        global_flags: List[str] = []
        if isinstance(authority, ResolvedMuleRunTokenCredential):
            global_flags += ["--token", authority.secret]
        global_flags += ["--base-url", routes.login_base_url, "--site", routes.login_site]
        studio_flags = ["--base-url", routes.studio_base_url, "--site", routes.studio_site]
        return global_flags, studio_flags

    if env is None:
        return [], []

    global_flags = mulerun_global_flags(env)
    studio_flags: List[str] = []
    router_base = env.get("MULEROUTER_BASE_URL") or _get_base_url_for_model("", env)
    studio_flags += ["--base-url", router_base]
    studio_flags += ["--site", _get_site(env)]

    return global_flags, studio_flags


def _sensitive_cli_values(
    env: Mapping[str, str] | None = None,
    *,
    authority: ResolvedMuleRunTokenCredential | ResolvedMuleRunProfileCredential | None = None,
) -> List[str]:
    values = [
        value for key in ("MULERUN_TOKEN", "MULEROUTER_API_KEY")
        if env is not None and (value := env.get(key))
    ]
    if isinstance(authority, ResolvedMuleRunTokenCredential):
        values.append(authority.secret)
    return values


def _redact_command_for_log(cmd: List[str]) -> List[str]:
    redacted = list(cmd)
    for index, value in enumerate(redacted[:-1]):
        if value in {"--token", "--api-key"}:
            redacted[index + 1] = "<redacted>"
    return redacted


def _redact_text(
    value: str,
    *,
    env: Mapping[str, str] | None = None,
    authority: ResolvedMuleRunTokenCredential | ResolvedMuleRunProfileCredential | None = None,
) -> str:
    redacted = value
    for secret in _sensitive_cli_values(env, authority=authority):
        redacted = redacted.replace(secret, "<redacted>")
    return redacted


def _require_bound_mulerun_profile_snapshot(authority: ResolvedMuleRunProfileCredential) -> None:
    profile = authority.profile_home / ".mulerun"
    try:
        if not profile.is_dir():
            raise MuleRunProfileError("Bound MuleRun profile snapshot is unavailable")
        if not any(path.is_file() for path in profile.rglob("*")):
            raise MuleRunProfileError("Bound MuleRun profile snapshot is empty")
        actual = fingerprint_owned_mulerun_profile(authority.profile_home)
    except (OSError, MuleRunProfileError) as exc:
        raise RuntimeError("Bound MuleRun profile snapshot is unavailable") from exc
    if actual != authority.profile_fingerprint:
        raise RuntimeError("Bound MuleRun profile snapshot is unavailable")


def _run_mulerun_studio(
    endpoint: str,
    args: List[str],
    timeout: int = MAX_WAIT,
    *,
    env: Mapping[str, str] | None = None,
    authority: ResolvedMuleRunTokenCredential | ResolvedMuleRunProfileCredential | None = None,
) -> Dict[str, Any]:
    """Execute `mulerun studio run <endpoint> --json` and parse result."""
    global_flags, studio_flags = _mulerun_control_flags(env, authority=authority)
    cmd = ["mulerun"] + global_flags + ["studio", "run", endpoint, "--json"] + studio_flags + args

    if authority is None:
        if env is None:
            context = nullcontext(None)
        elif env.get("MULERUN_TOKEN"):
            context = tempfile.TemporaryDirectory(prefix="lumenx-mulerun-token-")
        else:
            profile_home = mulerun_profile_home(env)
            if profile_home is None:
                raise RuntimeError("App-owned MuleRun login profile is required for CLI generation")
            context = snapshot_mulerun_profile_to_temp_home(profile_home)
    elif isinstance(authority, ResolvedMuleRunTokenCredential):
        context = tempfile.TemporaryDirectory(prefix="lumenx-mulerun-bound-token-")
    elif isinstance(authority, ResolvedMuleRunProfileCredential):
        context = nullcontext(authority.profile_home)
    else:
        raise RuntimeError("Unsupported MuleRun CLI authority")

    with context as profile_root:
        if authority is None:
            if env is not None and env.get("MULERUN_TOKEN"):
                assert profile_root is not None
                profile_root = Path(profile_root) / "home"
            child_env = _build_mulerun_subprocess_env(env, profile_root=profile_root)
        elif isinstance(authority, ResolvedMuleRunTokenCredential):
            assert profile_root is not None
            profile_root = Path(profile_root) / "home"
            child_env = _build_mulerun_subprocess_env(
                {"MULERUN_TOKEN": authority.secret}, profile_root=profile_root
            )
        else:
            _require_bound_mulerun_profile_snapshot(authority)
            child_env = _build_mulerun_subprocess_env({}, profile_root=profile_root, create_dirs=False)

        logger.info("[MuleRun CLI] %s", " ".join(_redact_command_for_log(cmd)))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 60, env=child_env
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"MuleRun CLI timed out after {timeout + 60}s")

    if result.returncode != 0:
        stderr = _redact_text(result.stderr.strip(), env=env, authority=authority)
        raise RuntimeError(f"MuleRun CLI failed (exit {result.returncode}): {stderr[:500]}")

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("MuleRun CLI returned empty output")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            redacted_stdout = _redact_text(stdout, env=env, authority=authority)
            raise RuntimeError(f"MuleRun CLI returned non-JSON output: {redacted_stdout[:300]}")

    return data


def _resolve_local_image_path(
    img_url: Optional[str] = None,
    img_path: Optional[str] = None,
    *,
    output_root: Optional[str] = None,
) -> Optional[str]:
    """Resolve image to a local file path for CLI --image flag."""
    if output_root:
        ref = img_path or img_url
        if isinstance(ref, str) and ref.startswith(("http://", "https://")):
            return ref
        if isinstance(ref, str):
            local_path = resolve_local_media_path(ref, output_root=output_root)
            if local_path is None or not os.path.isfile(local_path):
                raise ValueError("Image reference is outside this runtime or does not exist")
            return local_path
        return None

    if img_path and os.path.exists(img_path):
        return os.path.abspath(img_path)
    if img_url:
        if img_url.startswith(("http://", "https://")):
            return img_url
        potential = os.path.join("output", img_url)
        if os.path.exists(potential):
            return os.path.abspath(potential)
        if os.path.exists(img_url):
            return os.path.abspath(img_url)
    return None


# ---------------------------------------------------------------------------
# MuleRouter HTTP API layer (original)
# ---------------------------------------------------------------------------

def _get_api_key(env: Mapping[str, str] | None = None) -> str:
    environment = os.environ if env is None else env
    key = environment.get("MULEROUTER_API_KEY", "")
    return key


def _require_api_key(env: Mapping[str, str] | None = None) -> str:
    key = _get_api_key(env)
    if not key:
        raise RuntimeError("MULEROUTER_API_KEY not set in environment")
    return key


def _auth_headers(env: Mapping[str, str] | None = None) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_api_key(env)}",
        "Content-Type": "application/json",
    }


def _encode_file_to_data_uri(file_path: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    if not mime:
        mime = "application/octet-stream"
    with open(file_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{data}"


def _resolve_image_input(
    img_url: Optional[str] = None,
    img_path: Optional[str] = None,
    *,
    output_root: Optional[str] = None,
) -> Optional[str]:
    """Resolve image to a URL or base64 data URI for MuleRouter."""
    if output_root:
        ref = img_path or img_url
        if isinstance(ref, str) and ref.startswith(("http://", "https://", "data:")):
            return ref
        if isinstance(ref, str):
            local_path = resolve_local_media_path(ref, output_root=output_root)
            if local_path and os.path.isfile(local_path):
                return _encode_file_to_data_uri(local_path)
            raise ValueError("Image reference is outside this runtime or does not exist")
        return None

    if img_path and os.path.exists(img_path):
        return _encode_file_to_data_uri(img_path)
    if img_url:
        if img_url.startswith(("http://", "https://", "data:")):
            return img_url
        potential = os.path.join("output", img_url)
        if os.path.exists(potential):
            return _encode_file_to_data_uri(potential)
        if os.path.exists(img_url):
            return _encode_file_to_data_uri(img_url)
    return None


def _submit_task(
    base_url: str,
    api_path: str,
    body: Dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    retry_uncertain: bool = True,
) -> str:
    """Submit a generation task and return the task ID."""
    url = f"{base_url}{api_path}"
    logger.info(f"[MuleRouter] POST {api_path}")
    resp = _request_with_retry("POST", url, max_retries=3 if retry_uncertain else 1,
                               headers=_auth_headers(env), json=body, timeout=60)
    data = resp.json()

    task_info = data.get("task_info") or data
    task_id = task_info.get("id") or task_info.get("task_id")
    if not task_id:
        raise RuntimeError(f"MuleRouter: no task_id in response: {data}")

    logger.info(f"[MuleRouter] Task submitted: {task_id}")
    return task_id


def _request_with_retry(method: str, url: str, max_retries: int = 3, **kwargs) -> requests.Response:
    """HTTP request with exponential backoff retry on transient errors."""
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                wait = min(2 ** attempt * 5, 60)
                logger.warning(f"[MuleRouter] HTTP {resp.status_code}, retry in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.ConnectionError:
            if attempt < max_retries - 1:
                wait = min(2 ** attempt * 5, 60)
                logger.warning(f"[MuleRouter] Connection error, retry in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("MuleRouter: max retries exceeded")


def _poll_task(
    base_url: str,
    api_path: str,
    task_id: str,
    *,
    env: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Poll a task until completion with retry on transient errors."""
    poll_url = f"{base_url}{api_path}/{task_id}"
    elapsed = 0

    while elapsed < MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        resp = _request_with_retry("GET", poll_url, headers=_auth_headers(env), timeout=30)
        data = resp.json()

        task_info = data.get("task_info") or data
        status = (task_info.get("status") or "").lower()
        logger.info(f"[MuleRouter] Poll status: {status} ({elapsed}s)")

        if status in ("completed", "succeeded", "success"):
            return data
        elif status in ("failed", "error", "cancelled", "canceled"):
            msg = task_info.get("error") or task_info.get("message") or "unknown error"
            raise RuntimeError(f"MuleRouter task {status}: {msg}")

    raise RuntimeError(f"MuleRouter task timed out after {MAX_WAIT}s")


def _download_file(url: str, output_path: str) -> str:
    """Download a file from URL to local path with retry."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    resp = _request_with_retry("GET", url, timeout=120, stream=True)
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    return output_path


class MuleRouterVideoModel(VideoGenModel):
    """Seedance 2.0 video generation via MuleRun CLI or MuleRouter HTTP API."""

    def __init__(self, config: Dict[str, Any], *, env: Mapping[str, str] | None = None):
        super().__init__(config)
        self._env = os.environ if env is None else MappingProxyType(dict(env))
        self.use_fast = self.config.get("params", {}).get("fast", False)

    def _get_api_key(self) -> str:
        return _get_api_key(self._env)

    def _get_site(self) -> str:
        return _get_site(self._env)

    def _get_base_url_for_model(self, model_prefix: str) -> str:
        return _get_base_url_for_model(model_prefix, self._env)

    def _use_cli_backend(self) -> bool:
        return _use_cli_backend(self._env)

    def _auth_headers(self) -> Dict[str, str]:
        return _auth_headers(self._env)

    def _resolve_local_image_path(
        self,
        img_url: Optional[str] = None,
        img_path: Optional[str] = None,
    ) -> Optional[str]:
        return _resolve_local_image_path(img_url, img_path, output_root=self.output_root)

    def _resolve_image_input(
        self,
        img_url: Optional[str] = None,
        img_path: Optional[str] = None,
    ) -> Optional[str]:
        return _resolve_image_input(img_url, img_path, output_root=self.output_root)

    def generate(self, prompt: str, output_path: str, img_url: Optional[str] = None,
                 img_path: Optional[str] = None, **kwargs) -> Tuple[str, float]:
        if self._use_cli_backend():
            return self._generate_via_cli(prompt, output_path, img_url, img_path, **kwargs)
        return self._generate_via_http(prompt, output_path, img_url, img_path, **kwargs)

    def _generate_via_cli(self, prompt: str, output_path: str, img_url: Optional[str] = None,
                          img_path: Optional[str] = None, **kwargs) -> Tuple[str, float]:
        """Generate video via mulerun studio run CLI."""
        start_time = time.time()
        duration = kwargs.get("duration", 5)
        seed = kwargs.get("seed")
        resolution = kwargs.get("resolution", "1080p")
        watermark = kwargs.get("watermark", False)

        generation_mode = kwargs.get("generation_mode", "")
        ref_image_urls: List[str] = kwargs.get("ref_image_urls") or []

        is_r2v = generation_mode == "r2v" or bool(ref_image_urls)
        is_i2v = bool(img_url or img_path) and not is_r2v

        speed = "-fast" if self.use_fast else ""
        model_base = f"bytedance/seedance-2.0{speed}"

        if is_r2v:
            endpoint = f"{model_base}/reference-to-video"
        elif is_i2v:
            endpoint = f"{model_base}/image-to-video"
        else:
            endpoint = f"{model_base}/text-to-video"

        args = ["--prompt", prompt, "--duration", str(duration)]
        if resolution:
            size = "1920*1080" if resolution == "1080p" else "1280*720"
            args += ["--size", size]
        if seed is not None:
            args += ["--seed", str(seed)]
        if not watermark:
            args += ["--extra", "watermark=false"]

        if is_i2v:
            image_path = self._resolve_local_image_path(img_url, img_path)
            if image_path:
                args += ["--image", image_path]
        elif is_r2v:
            primary = self._resolve_local_image_path(img_url, img_path)
            if primary:
                args += ["--image", primary]
            for index, ref_url in enumerate(ref_image_urls):
                ref_path = self._resolve_local_image_path(ref_url)
                if ref_path:
                    if index == 0 and primary and ref_path == primary:
                        continue
                    args += ["--extra", f"reference_image={ref_path}"]

        args += ["--max-wait", "1800"]
        result = _run_mulerun_studio(endpoint, args, timeout=1800, env=self._env)
        video_url = self._extract_video_url(result)
        _download_file(video_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRun/Seedance] CLI done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

    def _generate_via_http(self, prompt: str, output_path: str, img_url: Optional[str] = None,
                           img_path: Optional[str] = None, **kwargs) -> Tuple[str, float]:
        """Generate video via MuleRouter HTTP API (original path)."""
        start_time = time.time()
        base_url = self._get_base_url_for_model("bytedance/seedance")

        duration = kwargs.get("duration", 5)
        seed = kwargs.get("seed")
        resolution = kwargs.get("resolution", "1080p")
        aspect_ratio = kwargs.get("aspect_ratio", "16:9")
        watermark = kwargs.get("watermark", False)

        generation_mode = kwargs.get("generation_mode", "")
        ref_image_urls: List[str] = kwargs.get("ref_image_urls") or []

        is_r2v = generation_mode == "r2v" or bool(ref_image_urls)
        is_i2v = bool(img_url or img_path) and not is_r2v

        suffix = "-fast" if self.use_fast else ""

        if is_r2v:
            api_path = SEEDANCE_API_PATHS[f"r2v{suffix}"]
            body = self._build_r2v_body(prompt, img_url, img_path, ref_image_urls,
                                        duration, resolution, aspect_ratio, seed, watermark)
        elif is_i2v:
            api_path = SEEDANCE_API_PATHS[f"i2v{suffix}"]
            body = self._build_i2v_body(prompt, img_url, img_path,
                                        duration, resolution, aspect_ratio, seed, watermark)
        else:
            api_path = SEEDANCE_API_PATHS[f"t2v{suffix}"]
            body = self._build_t2v_body(prompt, duration, resolution, aspect_ratio, seed, watermark)

        task_id = _submit_task(base_url, api_path, body, env=self._env)
        result = _poll_task(base_url, api_path, task_id, env=self._env)

        video_url = self._extract_video_url(result)
        _download_file(video_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRouter/Seedance] HTTP done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

    def _build_t2v_body(self, prompt: str, duration: int, resolution: str,
                        aspect_ratio: str, seed: Optional[int], watermark: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "watermark": watermark,
        }
        if resolution:
            body["resolution"] = resolution
        if seed is not None:
            body["seed"] = seed
        return body

    def _build_i2v_body(self, prompt: str, img_url: Optional[str], img_path: Optional[str],
                        duration: int, resolution: str, aspect_ratio: str,
                        seed: Optional[int], watermark: bool) -> Dict[str, Any]:
        image = self._resolve_image_input(img_url, img_path)
        if not image:
            raise ValueError("Seedance I2V requires an input image")
        body: Dict[str, Any] = {
            "prompt": prompt,
            "image": image,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "watermark": watermark,
        }
        if resolution:
            body["resolution"] = resolution
        if seed is not None:
            body["seed"] = seed
        return body

    def _build_r2v_body(self, prompt: str, img_url: Optional[str], img_path: Optional[str],
                        ref_image_urls: List[str], duration: int, resolution: str,
                        aspect_ratio: str, seed: Optional[int], watermark: bool) -> Dict[str, Any]:
        images: List[str] = []
        primary = self._resolve_image_input(img_url, img_path)
        if primary:
            images.append(primary)
        for index, ref_url in enumerate(ref_image_urls):
            resolved = self._resolve_image_input(ref_url)
            if resolved:
                if index == 0 and primary and resolved == primary:
                    continue
                images.append(resolved)
        if not images:
            raise ValueError("Seedance R2V requires at least one reference image")
        body: Dict[str, Any] = {
            "prompt": prompt,
            "reference_images": images,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "watermark": watermark,
        }
        if resolution:
            body["resolution"] = resolution
        if seed is not None:
            body["seed"] = seed
        return body

    def _extract_video_url(self, result: Dict[str, Any]) -> str:
        videos = result.get("videos") or []
        if videos:
            v = videos[0]
            if isinstance(v, str):
                return v
            url = v.get("url") or v.get("video_url")
            if url:
                return url
        task_info = result.get("task_info") or result
        output = task_info.get("output") or {}
        if isinstance(output, dict):
            url = output.get("video_url") or output.get("url")
            if url:
                return url
        data = result.get("data") or {}
        data_videos = data.get("videos") or []
        if data_videos:
            v = data_videos[0]
            return v if isinstance(v, str) else (v.get("url") or v.get("video_url", ""))
        raise RuntimeError(f"MuleRouter: cannot extract video URL from result: {result}")


class MuleRouterImageModel(ImageGenModel):
    """GPT-Image-2 image generation and editing via MuleRun CLI or MuleRouter HTTP API."""

    def __init__(self, config: Dict[str, Any], *, env: Mapping[str, str] | None = None):
        super().__init__(config)
        self._env = os.environ if env is None else MappingProxyType(dict(env))

    def _get_base_url_for_model(self, model_prefix: str) -> str:
        return _get_base_url_for_model(model_prefix, self._env)

    def _use_cli_backend(self) -> bool:
        return _use_cli_backend(self._env)

    def _resolve_local_image_path(self, path: str) -> Optional[str]:
        return _resolve_local_image_path(img_path=path, img_url=path, output_root=self.output_root)

    def _resolve_image_input(self, path: str) -> Optional[str]:
        return _resolve_image_input(path, output_root=self.output_root)

    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        if self._use_cli_backend():
            return self._generate_via_cli(prompt, output_path, **kwargs)
        return self._generate_via_http(prompt, output_path, **kwargs)

    def generate_with_bound_cli(
        self,
        prompt: str,
        output_path: str,
        credential: ResolvedMuleRunTokenCredential | ResolvedMuleRunProfileCredential,
        **kwargs,
    ) -> Tuple[str, float]:
        """Generate image through an already bound MuleRun CLI authority."""
        if not isinstance(credential, (ResolvedMuleRunTokenCredential, ResolvedMuleRunProfileCredential)):
            raise RuntimeError("Unsupported MuleRun CLI credential")
        if "image_contract_version" in kwargs:
            validate_gpt_image2_edit_scope(credential.scope)
        return self._generate_via_cli(prompt, output_path, authority=credential, **kwargs)

    def generate_with_bound_http(
        self, prompt: str, output_path: str, credential, **kwargs,
    ) -> Tuple[str, float]:
        """Generate image through an already bound MuleRouter HTTP authority."""
        from .provider_tasks import ResolvedCredential

        if not isinstance(credential, ResolvedCredential):
            raise RuntimeError("Unsupported MuleRouter HTTP credential")
        scope = credential.scope
        if (
            scope.provider != "mulerouter"
            or scope.backend != "mulerouter"
            or scope.gateway != "mulerouter_http"
        ):
            raise RuntimeError("Unsupported MuleRouter HTTP scope")
        if "image_contract_version" in kwargs:
            validate_gpt_image2_edit_scope(scope)
        return self._generate_via_http(
            prompt, output_path,
            env={"MULEROUTER_API_KEY": credential.secret, "MULEROUTER_BASE_URL": scope.base_url},
            base_url=scope.base_url,
            _bound_http=True,
            **kwargs,
        )

    def submit_with_bound_http(
        self, prompt: str, credential: _image_tasks.ResolvedCredential, *,
        on_submitted: ImageReceiptWriter, **kwargs,
    ) -> _image_tasks.ProviderHandle:
        """One opt-in image POST followed by synchronous handle persistence only."""
        validate_receipt_writer(on_submitted)
        if not isinstance(credential, _image_tasks.ResolvedCredential):
            raise _image_tasks.ProviderBindingError("Explicit GPT HTTP credential required")
        contract = (
            _image_tasks.ProviderImageQueryContract.GPT_IMAGE2_EDIT if "image_contract_version" in kwargs
            else _image_tasks.ProviderImageQueryContract.GPT_IMAGE2_GENERATION
        )
        _image_tasks.validate_image_query_contract(contract, "gpt-image-2", credential.scope)
        if contract is _image_tasks.ProviderImageQueryContract.GPT_IMAGE2_EDIT:
            size, references = self._bound_edit_reference(prompt, kwargs)
            body = gpt_image2_edit_payload(
                prompt, size, [_encode_file_to_data_uri(references[0])],
                contract_version=kwargs["image_contract_version"],
            )
            api_path = GPT_IMAGE_API_PATHS["edit"]
        else:
            if (
                not isinstance(prompt, str) or not prompt.strip()
                or set(kwargs) - {"size", "quality"} or "size" not in kwargs
                or type(kwargs["size"]) is not str or kwargs["size"] not in GPT_IMAGE_VALID_SIZES
                or type(kwargs.get("quality", "high")) is not str
                or kwargs.get("quality", "high") not in {"high", "medium", "low", "auto"}
            ):
                raise _image_tasks.ProviderBindingError("Unsupported explicit GPT generation arguments")
            try:
                prompt.encode("utf-8")
            except UnicodeEncodeError:
                raise _image_tasks.ProviderBindingError("Invalid GPT image prompt encoding") from None
            body = self._build_generation_body(
                prompt=prompt, size=kwargs["size"], quality=kwargs.get("quality", "high"), n=1,
            )
            api_path = GPT_IMAGE_API_PATHS["generation"]
        submitted_at = time.time()
        status_code, result = request_json_envelope(
            "POST", credential.scope.base_url + api_path,
            {"Authorization": f"Bearer {credential.secret}", "Content-Type": "application/json"}, body,
        )
        info = result.get("task_info")
        task_id = info.get("id") if isinstance(info, dict) else None
        if not isinstance(task_id, str):
            raise _image_tasks.ProviderSubmitError("missing_receipt", status_code=status_code)
        try:
            handle = _image_tasks.ProviderHandle(
                schema_version=2, provider_model_id="gpt-image-2", task_id=task_id,
                scope=credential.scope, credential_ref=credential.credential_ref,
                query_capability=_image_tasks.QueryCapability.BY_TASK_ID,
                query_contract=contract, submitted_at=submitted_at,
            )
        except _image_tasks.ProviderBindingError:
            raise _image_tasks.ProviderSubmitError("missing_receipt", status_code=status_code) from None
        # 202 is documented. A usable ID on another status is preserved only as
        # defensive receipt retention, never as proof of generation success.
        write_submitted_handle(handle, on_submitted)
        return handle

    @staticmethod
    def query_with_bound_http(
        handle: _image_tasks.ProviderHandle, *, credential: _image_tasks.ResolvedCredential,
    ) -> _image_tasks.ProviderObservation:
        """One GET of the original generation/edit path, with mandatory UUID echo."""
        if not isinstance(handle, _image_tasks.ProviderHandle):
            raise _image_tasks.ProviderBindingError("Explicit image handle required")
        handle = _image_tasks.ProviderHandle.from_dict(handle.to_dict())
        scope = _image_tasks.validate_query_binding(handle, credential, media_kind="image")
        if handle.query_contract not in {
            _image_tasks.ProviderImageQueryContract.GPT_IMAGE2_GENERATION,
            _image_tasks.ProviderImageQueryContract.GPT_IMAGE2_EDIT,
        }:
            raise _image_tasks.ProviderBindingError("GPT image query requires its original contract")
        status_code, result = request_json_envelope(
            "GET", scope.base_url + _image_tasks.image_query_path(handle),
            {"Authorization": f"Bearer {credential.secret}"},
        )
        if status_code not in {200, 400}:
            raise _image_tasks.ProviderQueryError("http", status_code=status_code)
        info = result.get("task_info")
        if not isinstance(info, dict) or info.get("id") != handle.task_id:
            raise _image_tasks.ProviderQueryError("protocol")
        raw_status = info.get("status")
        if not isinstance(raw_status, str):
            raise _image_tasks.ProviderQueryError("protocol")
        try:
            status = _image_tasks.image_status_from_raw(handle, raw_status)
            if status_code == 400 and (
                status is not _image_tasks.ProviderTaskStatus.FAILED or not isinstance(info.get("error"), dict)
            ):
                raise _image_tasks.ProviderQueryError("protocol")
            codes = []
            for owner, key in ((info, "code"), (result, "error_code")):
                if "error" not in owner:
                    continue
                error = owner["error"]
                if not isinstance(error, dict) or status is not _image_tasks.ProviderTaskStatus.FAILED:
                    raise _image_tasks.ProviderQueryError("protocol")
                if any(name in error and not isinstance(error[name], str) for name in ("title", "detail")):
                    raise _image_tasks.ProviderQueryError("protocol")
                if key in error:
                    code = error[key]
                    if type(code) is not int or not 0 <= code <= 2147483647:
                        raise _image_tasks.ProviderQueryError("protocol")
                    codes.append(str(code))
            if len(set(codes)) > 1:
                raise _image_tasks.ProviderQueryError("protocol")
            image_url = None
            if status is _image_tasks.ProviderTaskStatus.SUCCEEDED:
                images = result.get("images")
                if not isinstance(images, list) or len(images) != 1:
                    raise _image_tasks.ProviderQueryError("protocol")
                image_url = images[0]
                _image_tasks.validate_result_url(image_url)
                if urlsplit(image_url).scheme != "https":
                    raise _image_tasks.ProviderQueryError("protocol")
            elif result.get("images") not in (None, []):
                raise _image_tasks.ProviderQueryError("protocol")
            return _image_tasks.ProviderObservation(
                task_id=info["id"], status=status, raw_status=raw_status,
                source_handle=handle, image_url=image_url, error_code=codes[0] if codes else None,
            )
        except _image_tasks.ProviderBindingError:
            raise _image_tasks.ProviderQueryError("protocol") from None

    def _bound_edit_reference(self, prompt: str, kwargs: Mapping[str, Any]) -> Tuple[str, List[str]]:
        """A marked Edit must never coerce fields or fall back to generation."""
        if (
            kwargs.get("image_contract_version") not in GPT_IMAGE2_EDIT_CONTRACT_VERSIONS
            or set(kwargs) != {"image_contract_version", "size", "ref_image_paths"}
        ):
            raise ValueError("Unsupported bound GPT Edit arguments")
        size = kwargs["size"]
        validate_gpt_image2_edit_parameters(prompt, {"size": size}, contract_version=kwargs["image_contract_version"])
        refs = kwargs["ref_image_paths"]
        if not isinstance(refs, (tuple, list)) or len(refs) != 1 or not isinstance(refs[0], str):
            raise ValueError("Bound GPT Edit requires one local reference")
        local = self._resolve_local_image_path(refs[0])
        if local is None or not os.path.isfile(local):
            raise ValueError("Bound GPT Edit reference is unavailable")
        return size, [local]

    def _generate_via_cli(
        self,
        prompt: str,
        output_path: str,
        *,
        authority: ResolvedMuleRunTokenCredential | ResolvedMuleRunProfileCredential | None = None,
        **kwargs,
    ) -> Tuple[str, float]:
        """Generate image via mulerun studio run CLI."""
        start_time = time.time()
        if "image_contract_version" in kwargs:
            if authority is None:
                raise ValueError("GPT Edit contract requires a bound authority")
            size, ref_image_paths = self._bound_edit_reference(prompt, kwargs)
            quality = None
        else:
            size = _normalize_gpt_image_size(kwargs.get("size", "1024x1024"))
            quality = kwargs.get("quality")
            ref_image_path = kwargs.get("ref_image_path")
            ref_image_paths = kwargs.get("ref_image_paths") or []
            if ref_image_path:
                ref_image_paths = [ref_image_path] + ref_image_paths

        if ref_image_paths:
            endpoint = "openai/gpt-image-2/edit"
        else:
            endpoint = "openai/gpt-image-2/generation"

        args = ["--prompt", prompt, "--size", size]
        if quality is not None:
            args += ["--quality", str(quality)]

        resolved_images = []
        for path in ref_image_paths:
            resolved = self._resolve_local_image_path(path)
            if resolved:
                resolved_images.append(resolved)
        if "image_contract_version" in kwargs and len(resolved_images) != 1:
            raise ValueError("Bound GPT Edit requires one resolved image")
        if resolved_images:
            import json as _json
            args += ["--images", _json.dumps(resolved_images)]

        result = _run_mulerun_studio(
            endpoint,
            args,
            timeout=300,
            env=None if authority is not None else self._env,
            authority=authority,
        )
        image_url = self._extract_image_url(result)
        _download_file(image_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRun/GPT-Image-2] CLI done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

    def _generate_via_http(self, prompt: str, output_path: str, *, _bound_http: bool = False, **kwargs) -> Tuple[str, float]:
        """Generate image via MuleRouter HTTP API (original path)."""
        start_time = time.time()
        bound_env = kwargs.pop("env", self._env)
        base_url = kwargs.pop("base_url", None) or self._get_base_url_for_model("openai/gpt-image-2")

        if "image_contract_version" in kwargs:
            if not _bound_http:
                raise ValueError("GPT Edit contract requires a bound authority")
            size, ref_image_paths = self._bound_edit_reference(prompt, kwargs)
            body = gpt_image2_edit_payload(
                prompt, size, [_encode_file_to_data_uri(ref_image_paths[0])],
                contract_version=kwargs["image_contract_version"],
            )
            api_path = GPT_IMAGE_API_PATHS["edit"]
        else:
            size = _normalize_gpt_image_size(kwargs.get("size", "1024x1024"))
            quality = kwargs.get("quality", "high")
            n = kwargs.get("n", 1)
            body = self._build_generation_body(prompt=prompt, size=size, quality=quality, n=n)
            ref_image_path = kwargs.get("ref_image_path")
            ref_image_paths = kwargs.get("ref_image_paths") or []
            if ref_image_path:
                ref_image_paths = [ref_image_path] + ref_image_paths
            if ref_image_paths:
                api_path = GPT_IMAGE_API_PATHS["edit"]
                body = self._build_edit_body(
                    prompt=prompt,
                    ref_image_paths=ref_image_paths,
                    size=size,
                    quality=quality,
                    n=n,
                )
            else:
                api_path = GPT_IMAGE_API_PATHS["generation"]

        # A bound Core attempt cannot repeat a paid POST after an uncertain response.
        submit_options = {"retry_uncertain": False} if _bound_http else {}
        task_id = _submit_task(base_url, api_path, body, env=bound_env, **submit_options)
        result = _poll_task(base_url, api_path, task_id, env=bound_env)

        image_url = self._extract_image_url(result)
        _download_file(image_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRouter/GPT-Image-2] HTTP done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

    def _build_generation_body(self, *, prompt: str, size: str, quality: str, n: int) -> Dict[str, Any]:
        return {
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": n,
        }

    def _build_edit_body(
        self,
        *,
        prompt: str,
        ref_image_paths: List[str],
        size: str,
        quality: str,
        n: int,
    ) -> Dict[str, Any]:
        body = self._build_generation_body(prompt=prompt, size=size, quality=quality, n=n)
        images = []
        for path in ref_image_paths:
            resolved = self._resolve_image_input(path)
            if resolved:
                images.append(resolved)
        if images:
            body["image"] = images[0]
            if len(images) > 1:
                body["reference_images"] = images[1:]
        return body

    def _extract_image_url(self, result: Dict[str, Any]) -> str:
        images = result.get("images") or []
        if images:
            v = images[0]
            if isinstance(v, str):
                return v
            url = v.get("url") or v.get("image_url")
            if url:
                return url
        task_info = result.get("task_info") or result
        output = task_info.get("output") or {}
        if isinstance(output, dict):
            url = output.get("image_url") or output.get("url")
            if url:
                return url
        data = result.get("data") or {}
        data_images = data.get("images") or []
        if data_images:
            v = data_images[0]
            return v if isinstance(v, str) else (v.get("url") or v.get("image_url", ""))
        raise RuntimeError(f"MuleRouter: cannot extract image URL from result: {result}")
