"""MuleRouter/MuleRun provider adapter for Seedance 2.0 (video) and GPT-Image-2 (image).

Supports two backends:
- MuleRun CLI mode: uses `mulerun studio run` subprocess (auth via MULERUN_TOKEN or browser login)
- MuleRouter HTTP mode: direct API calls to api.mulerouter.ai (auth via MULEROUTER_API_KEY)

Priority: MULERUN_TOKEN / CLI available → CLI mode; else → HTTP API mode.
"""

import base64
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .base import VideoGenModel
from .image import ImageGenModel

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

SEEDANCE_CLI_RESOLUTION_SIZES = {
    "480p": "832*480",
    "720p": "1280*720",
    "1080p": "1920*1080",
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

def _get_site() -> str:
    """Get the configured site. Default: mulerouter (more models available)."""
    return (os.getenv("MULEROUTER_SITE") or "mulerouter").strip().lower()


def _get_base_url_for_model(model_prefix: str) -> str:
    """Route to the correct site based on model availability."""
    for prefix in MULEROUTER_ONLY_MODELS:
        if model_prefix.startswith(prefix):
            return SITE_BASE_URLS["mulerouter"]
    site = _get_site()
    custom = os.getenv("MULEROUTER_BASE_URL")
    if custom:
        return custom.rstrip("/")
    return SITE_BASE_URLS.get(site, SITE_BASE_URLS["mulerouter"])


# ---------------------------------------------------------------------------
# Backend detection: MuleRun CLI vs MuleRouter HTTP API
# ---------------------------------------------------------------------------

def _is_mulerun_cli_available() -> bool:
    """Check if MuleRun CLI is usable (installed + authenticated)."""
    if shutil.which("mulerun") is not None:
        try:
            result = subprocess.run(
                ["mulerun", "login", "status"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    return False


_cli_available_cache: Optional[bool] = None


def _use_cli_backend() -> bool:
    """Determine whether to use CLI or HTTP API backend.

    Priority: MULEROUTER_API_KEY (HTTP) > CLI fallback.
    CLI is used only when no API key is configured and mulerun is logged in.
    """
    global _cli_available_cache
    if os.getenv("MULEROUTER_API_KEY"):
        return False
    if _cli_available_cache is None:
        _cli_available_cache = _is_mulerun_cli_available()
    return _cli_available_cache


# ---------------------------------------------------------------------------
# MuleRun CLI execution layer
# ---------------------------------------------------------------------------

def _run_mulerun_studio(endpoint: str, args: List[str], timeout: int = MAX_WAIT) -> Dict[str, Any]:
    """Execute `mulerun studio run <endpoint> --json` and parse result."""
    cmd = ["mulerun", "studio", "run", endpoint, "--json"] + args

    env = os.environ.copy()
    token = os.getenv("MULERUN_TOKEN")
    if token:
        env["MULERUN_TOKEN"] = token

    logger.info(f"[MuleRun CLI] {' '.join(cmd[:6])}...")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 60, env=env
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"MuleRun CLI timed out after {timeout + 60}s")

    if result.returncode != 0:
        stderr = result.stderr.strip()
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
            raise RuntimeError(f"MuleRun CLI returned non-JSON output: {stdout[:300]}")

    return data


def _resolve_local_image_path(img_url: Optional[str] = None, img_path: Optional[str] = None) -> Optional[str]:
    """Resolve image to a local file path for CLI --image flag."""
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

def _get_api_key() -> str:
    key = os.getenv("MULEROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("MULEROUTER_API_KEY not set in environment")
    return key


def _auth_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


def _encode_file_to_data_uri(file_path: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    if not mime:
        mime = "application/octet-stream"
    with open(file_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{data}"


def _resolve_image_input(img_url: Optional[str] = None, img_path: Optional[str] = None) -> Optional[str]:
    """Resolve image to a URL or base64 data URI for MuleRouter."""
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


def _submit_task(base_url: str, api_path: str, body: Dict[str, Any]) -> str:
    """Submit a generation task and return the task ID."""
    url = f"{base_url}{api_path}"
    logger.info(f"[MuleRouter] POST {api_path}")
    resp = _request_with_retry("POST", url, headers=_auth_headers(), json=body, timeout=60)
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


def _poll_task(base_url: str, api_path: str, task_id: str) -> Dict[str, Any]:
    """Poll a task until completion with retry on transient errors."""
    poll_url = f"{base_url}{api_path}/{task_id}"
    elapsed = 0

    while elapsed < MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

        resp = _request_with_retry("GET", poll_url, headers=_auth_headers(), timeout=30)
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

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.use_fast = config.get("params", {}).get("fast", False)

    def generate(self, prompt: str, output_path: str, img_url: Optional[str] = None,
                 img_path: Optional[str] = None, **kwargs) -> Tuple[str, float]:
        if _use_cli_backend():
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
            size = SEEDANCE_CLI_RESOLUTION_SIZES.get(str(resolution).lower(), "1280*720")
            args += ["--size", size]
        if seed is not None:
            args += ["--seed", str(seed)]
        if not watermark:
            args += ["--extra", "watermark=false"]

        if is_i2v:
            image_path = _resolve_local_image_path(img_url, img_path)
            if image_path:
                args += ["--image", image_path]
        elif is_r2v:
            primary = _resolve_local_image_path(img_url, img_path)
            if primary:
                args += ["--image", primary]
            for ref_url in ref_image_urls:
                ref_path = _resolve_local_image_path(ref_url)
                if ref_path:
                    args += ["--extra", f"reference_image={ref_path}"]

        args += ["--max-wait", "1800"]
        result = _run_mulerun_studio(endpoint, args, timeout=1800)
        video_url = self._extract_video_url(result)
        _download_file(video_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRun/Seedance] CLI done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

    def _generate_via_http(self, prompt: str, output_path: str, img_url: Optional[str] = None,
                           img_path: Optional[str] = None, **kwargs) -> Tuple[str, float]:
        """Generate video via MuleRouter HTTP API (original path)."""
        start_time = time.time()
        base_url = _get_base_url_for_model("bytedance/seedance")

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

        task_id = _submit_task(base_url, api_path, body)
        result = _poll_task(base_url, api_path, task_id)

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
        image = _resolve_image_input(img_url, img_path)
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
        primary = _resolve_image_input(img_url, img_path)
        if primary:
            images.append(primary)
        for ref_url in ref_image_urls:
            resolved = _resolve_image_input(ref_url)
            if resolved:
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

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        if _use_cli_backend():
            return self._generate_via_cli(prompt, output_path, **kwargs)
        return self._generate_via_http(prompt, output_path, **kwargs)

    def _generate_via_cli(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        """Generate image via mulerun studio run CLI."""
        start_time = time.time()
        size = _normalize_gpt_image_size(kwargs.get("size", "1024x1024"))

        ref_image_path = kwargs.get("ref_image_path")
        ref_image_paths = kwargs.get("ref_image_paths") or []
        if ref_image_path:
            ref_image_paths = [ref_image_path] + ref_image_paths

        if ref_image_paths:
            endpoint = "openai/gpt-image-2/edit"
        else:
            endpoint = "openai/gpt-image-2/generation"

        args = ["--prompt", prompt, "--size", size]

        resolved_images = []
        for path in ref_image_paths:
            resolved = _resolve_local_image_path(img_path=path, img_url=path)
            if resolved:
                resolved_images.append(resolved)
        if resolved_images:
            import json as _json
            args += ["--images", _json.dumps(resolved_images)]

        result = _run_mulerun_studio(endpoint, args, timeout=300)
        image_url = self._extract_image_url(result)
        _download_file(image_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRun/GPT-Image-2] CLI done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

    def _generate_via_http(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        """Generate image via MuleRouter HTTP API (original path)."""
        start_time = time.time()
        base_url = _get_base_url_for_model("openai/gpt-image-2")

        size = _normalize_gpt_image_size(kwargs.get("size", "1024x1024"))
        quality = kwargs.get("quality", "high")
        n = kwargs.get("n", 1)

        body: Dict[str, Any] = {
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": n,
        }

        ref_image_path = kwargs.get("ref_image_path")
        ref_image_paths = kwargs.get("ref_image_paths") or []
        if ref_image_path:
            ref_image_paths = [ref_image_path] + ref_image_paths

        if ref_image_paths:
            api_path = GPT_IMAGE_API_PATHS["edit"]
            images = []
            for path in ref_image_paths:
                resolved = _resolve_image_input(path)
                if resolved:
                    images.append(resolved)
            if images:
                body["image"] = images[0]
                if len(images) > 1:
                    body["reference_images"] = images[1:]
        else:
            api_path = GPT_IMAGE_API_PATHS["generation"]

        task_id = _submit_task(base_url, api_path, body)
        result = _poll_task(base_url, api_path, task_id)

        image_url = self._extract_image_url(result)
        _download_file(image_url, output_path)

        generation_time = time.time() - start_time
        logger.info(f"[MuleRouter/GPT-Image-2] HTTP done in {generation_time:.1f}s -> {output_path}")
        return output_path, generation_time

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
