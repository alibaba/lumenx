"""Vidu video generation model adapter.

API: https://api.vidu.cn/ent/v2
Auth: Token header using VIDU_API_KEY
Models: viduq3-pro (default), viduq3-turbo (fast)

T-B3 参数层升级（T-V2 §2.2/§3.2#7, 端点经 ComfyUI 官方节点 + new-api 交叉印证）:
- reference2video: POST /ent/v2/reference2video, 1-7 张参考图,
  prompt 内 @主题名 寻址（subjects 口径: 每主体 ≤3 图, 总量 7）;
  支持 seed / movement_amplitude
- 首尾帧走独立 start-end2video 端点（images=[首帧, 尾帧]）,
  不与 reference2video 混用
"""

import logging
import os
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import requests

from .base import VideoGenModel
from ..utils.endpoints import get_provider_base_url
from ..utils.oss_utils import OSSImageUploader
from ..utils.provider_media import resolve_media_input

logger = logging.getLogger(__name__)
DEFAULT_T2V_MODEL = "viduq3-pro"
DEFAULT_I2V_MODEL = "viduq3-pro"
# reference2video / startend2video 的默认模型（new-api 规范化 viduq2 为 r2v;
# ComfyUI 官方节点 startend 列 viduq1）——kwargs.model 可覆盖
DEFAULT_R2V_MODEL = "viduq2"
DEFAULT_STARTEND_MODEL = "viduq1"

VIDU_MAX_REFERENCE_IMAGES = 7
VIDU_MAX_IMAGES_PER_SUBJECT = 3


def _validate_reference_inputs(
    ref_image_urls: List[str],
    ref_subjects: Optional[List[str]],
    prompt: str,
) -> None:
    """reference2video 入参校验（构造层, 不发请求）:
    1-7 张参考图; subjects 与图片一一对应且每主体 ≤3 张;
    prompt 未含 @主题名 时给 warning（Vidu 靠 @名字寻址, 缺失会退化为普通多图）。"""
    if not (1 <= len(ref_image_urls) <= VIDU_MAX_REFERENCE_IMAGES):
        raise ValueError(
            f"Vidu reference2video 需要 1..{VIDU_MAX_REFERENCE_IMAGES} 张参考图, "
            f"收到 {len(ref_image_urls)}"
        )
    if ref_subjects is None:
        return
    if len(ref_subjects) != len(ref_image_urls):
        raise ValueError(
            f"ref_subjects（{len(ref_subjects)}）必须与 ref_image_urls（{len(ref_image_urls)}）一一对应"
        )
    counts = Counter(s for s in ref_subjects if s)
    over = {s: c for s, c in counts.items() if c > VIDU_MAX_IMAGES_PER_SUBJECT}
    if over:
        raise ValueError(
            f"Vidu subjects 口径每主体最多 {VIDU_MAX_IMAGES_PER_SUBJECT} 张图, 超限: {over}"
        )
    for subject in sorted(set(s for s in ref_subjects if s)):
        if f"@{subject}" not in (prompt or ""):
            logger.warning(
                "[Vidu] 参考图主题 %r 未在 prompt 中以 @%s 寻址（@主题名寻址将不生效）",
                subject, subject,
            )


class ViduModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key") or os.getenv("VIDU_API_KEY", "")
        self.model_name = config.get("params", {}).get("model_name", DEFAULT_I2V_MODEL)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _map_status(raw_state: str) -> str:
        """Map Vidu API states to normalized statuses."""
        mapping = {
            "created": "pending",
            "queueing": "pending",
            "processing": "running",
            "success": "succeeded",
            "failed": "failed",
        }
        return mapping.get(raw_state.lower(), "pending")

    def _resolve_vendor_image_input(
        self,
        *,
        img_url: str = None,
        img_path: str = None,
        model_name: str = None,
    ) -> str:
        """
        Resolve Vidu vendor image input via the shared provider-media layer.

        Prefer an existing remote URL when available. For local files, require an
        OSS-backed signed URL and fail clearly if the current environment cannot
        provide one.
        """
        if isinstance(img_url, str) and img_url.startswith(("http://", "https://")):
            image_ref = img_url
        else:
            image_ref = img_path or img_url

        if not image_ref:
            raise ValueError("Vidu image input requires img_path or img_url")

        resolved = resolve_media_input(
            image_ref,
            model_name=model_name or self.model_name,
            modality="image",
            backend="vendor",
            uploader=OSSImageUploader(),
        )
        return resolved.value

    def generate(self, prompt: str, output_path: str, img_url: str = None,
                 img_path: str = None, **kwargs) -> Tuple[str, float]:
        """Generate video using Vidu API (T2V / I2V / reference2video / startend2video).

        kwargs 媒体参数（T-V2 §2.2 官方语义, 构造层校验）:
        - ref_image_urls: 1-7 张参考图 URL → reference2video 多参考模式
          （prompt 中 @主题名 寻址）
        - ref_subjects: 与 ref_image_urls 等长的主题名列表（每主体 ≤3 图, 校验用）
        - tail_img_url: 尾帧 URL → startend2video 首尾帧模式（必须与 img_url/img_path
          成对; 独立端点, 不能与 ref_image_urls 混用）
        """
        duration = kwargs.get("duration", 5)
        resolution = (kwargs.get("resolution") or "720p").lower()
        aspect_ratio = kwargs.get("aspect_ratio", "16:9")

        start_time = time.time()

        ref_image_urls: List[str] = kwargs.get("ref_image_urls") or []
        ref_subjects = kwargs.get("ref_subjects")
        tail_img_url = kwargs.get("tail_img_url")

        is_r2v = bool(ref_image_urls)
        is_startend = bool(tail_img_url)
        if is_r2v and is_startend:
            raise ValueError(
                "Vidu 首尾帧走独立 start-end2video 端点, 不能与 reference2video 多参考混用"
            )
        if is_startend and not (img_url or img_path):
            raise ValueError(
                "Vidu 首尾帧模式需要首帧（img_url/img_path）与尾帧（tail_img_url）成对传入"
            )

        base_url = get_provider_base_url("VIDU")

        if is_r2v:
            _validate_reference_inputs(ref_image_urls, ref_subjects, prompt)
            resolved_refs = [
                self._resolve_vendor_image_input(
                    img_url=ref, model_name=kwargs.get("model"),
                )
                for ref in ref_image_urls
            ]
            task_id, used_model = self._submit_reference2video(
                prompt=prompt,
                image_urls=resolved_refs,
                model=kwargs.get("model"),
                duration=duration,
                resolution=resolution,
                seed=kwargs.get("seed", 0),
                movement_amplitude=kwargs.get("movement_amplitude", "auto"),
                ref_subjects=ref_subjects,
            )
        elif is_startend:
            first_url = self._resolve_vendor_image_input(
                img_url=img_url, img_path=img_path, model_name=kwargs.get("model"),
            )
            last_url = self._resolve_vendor_image_input(
                img_url=tail_img_url, model_name=kwargs.get("model"),
            )
            task_id, used_model = self._submit_startend2video(
                prompt=prompt,
                first_image_url=first_url,
                last_image_url=last_url,
                model=kwargs.get("model"),
                duration=duration,
                resolution=resolution,
                seed=kwargs.get("seed", 0),
                movement_amplitude=kwargs.get("movement_amplitude", "auto"),
            )
        elif img_url or img_path:
            task_id, used_model = self._submit_i2v(
                prompt=prompt,
                image_url=self._resolve_vendor_image_input(
                    img_url=img_url,
                    img_path=img_path,
                    model_name=kwargs.get("model"),
                ),
                model=kwargs.get("model"),
                duration=duration,
                resolution=resolution,
                seed=kwargs.get("seed", 0),
                movement_amplitude=kwargs.get("movement_amplitude", "auto"),
                audio=kwargs.get("audio", True),
            )
        else:
            task_id, used_model = self._submit_t2v(
                prompt=prompt,
                model=kwargs.get("model"),
                duration=duration,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                seed=kwargs.get("seed", 0),
                style=kwargs.get("style", "general"),
                bgm=kwargs.get("bgm", True),
            )

        logger.info(f"[Vidu] Task submitted: {task_id} (model={used_model})")

        # Poll for completion
        poll_url = f"{base_url}/tasks/{task_id}/creations"
        max_wait = 600
        poll_interval = 10
        elapsed = 0

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            resp = requests.get(poll_url, headers=self._headers(), timeout=30)
            if resp.status_code not in (200, 201):
                logger.warning(f"[Vidu] Poll returned HTTP {resp.status_code}")
                continue

            data = resp.json()
            state = data.get("state", "unknown")
            normalized = self._map_status(state)
            logger.info(f"[Vidu] Task status: {state} -> {normalized} ({elapsed}s)")

            if normalized == "succeeded":
                video_url = data["creations"][0]["url"]
                # Download video
                video_content = requests.get(video_url, timeout=120).content
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(video_content)

                generation_time = time.time() - start_time
                logger.info(f"[Vidu] Done in {generation_time:.1f}s -> {output_path}")
                return output_path, generation_time

            elif normalized == "failed":
                raise RuntimeError(f"Vidu task failed: {data}")

        raise RuntimeError(f"Vidu task timed out after {max_wait}s")

    def _submit_t2v(self, *, prompt: str, model: str = None, duration: int = 5,
                    resolution: str = "720p", aspect_ratio: str = "16:9",
                    seed: int = 0, style: str = "general", bgm: bool = True,
                    ) -> Tuple[str, str]:
        """Submit a text-to-video task. Returns (task_id, model_used)."""
        used_model = model or DEFAULT_T2V_MODEL

        body: Dict[str, Any] = {
            "model": used_model,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "seed": seed,
            "style": style,
            "bgm": bgm,
        }

        submit_url = f"{get_provider_base_url('VIDU')}/text2video"
        logger.info(f"[Vidu] Submitting t2v task (model={used_model}, duration={duration}s)")

        resp = requests.post(submit_url, headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Vidu t2v submission failed (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in Vidu response: {data}")

        return task_id, used_model

    def _submit_i2v(self, *, prompt: str, image_url: str, model: str = None,
                    duration: int = 5, resolution: str = "720p",
                    seed: int = 0, movement_amplitude: str = "auto", audio: bool = True,
                    ) -> Tuple[str, str]:
        """Submit an image-to-video task. Returns (task_id, model_used)."""
        if not image_url:
            raise ValueError("image_url is required for i2v mode")

        used_model = model or DEFAULT_I2V_MODEL

        body: Dict[str, Any] = {
            "model": used_model,
            "images": [image_url],
            "prompt": prompt or "",
            "duration": duration,
            "resolution": resolution,
            "seed": seed,
            "movement_amplitude": movement_amplitude,
            "audio": audio,
        }

        submit_url = f"{get_provider_base_url('VIDU')}/img2video"
        logger.info(f"[Vidu] Submitting i2v task (model={used_model}, duration={duration}s)")

        resp = requests.post(submit_url, headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Vidu i2v submission failed (HTTP {resp.status_code}): {resp.text}")

        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in Vidu response: {data}")

        return task_id, used_model

    def _submit_reference2video(self, *, prompt: str, image_urls: List[str],
                                model: str = None, duration: int = 5,
                                resolution: str = "720p", seed: int = 0,
                                movement_amplitude: str = "auto",
                                ref_subjects: Optional[List[str]] = None,
                                ) -> Tuple[str, str]:
        """Submit a reference2video task (1-7 参考图, prompt @主题名寻址).

        请求体字段集经 ComfyUI 官方节点 + new-api 交叉印证:
        {model, images[], prompt, duration, resolution, movement_amplitude, seed}。
        ref_subjects 仅作本地校验/审计, 不进请求体（官方 images 为纯 URL 数组）。
        """
        if not image_urls:
            raise ValueError("image_urls is required for reference2video mode")

        used_model = model or DEFAULT_R2V_MODEL

        body: Dict[str, Any] = {
            "model": used_model,
            "images": list(image_urls),
            "prompt": prompt or "",
            "duration": duration,
            "resolution": resolution,
            "seed": seed,
            "movement_amplitude": movement_amplitude,
        }

        submit_url = f"{get_provider_base_url('VIDU')}/reference2video"
        logger.info(
            "[Vidu] Submitting reference2video task (model=%s, refs=%d, duration=%ss)",
            used_model, len(image_urls), duration,
        )

        resp = requests.post(submit_url, headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Vidu reference2video submission failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in Vidu response: {data}")

        return task_id, used_model

    def _submit_startend2video(self, *, prompt: str, first_image_url: str,
                               last_image_url: str, model: str = None,
                               duration: int = 5, resolution: str = "1080p",
                               seed: int = 0, movement_amplitude: str = "auto",
                               ) -> Tuple[str, str]:
        """Submit a start-end2video task (首尾帧独立端点, images=[首帧, 尾帧]).

        官方约束: 首尾帧走独立 Start End To Video 端点, 不与 reference2video 混用。
        """
        if not first_image_url or not last_image_url:
            raise ValueError(
                "startend2video requires both first_image_url and last_image_url"
            )

        used_model = model or DEFAULT_STARTEND_MODEL

        body: Dict[str, Any] = {
            "model": used_model,
            "images": [first_image_url, last_image_url],
            "prompt": prompt or "",
            "duration": duration,
            "resolution": resolution,
            "seed": seed,
            "movement_amplitude": movement_amplitude,
        }

        submit_url = f"{get_provider_base_url('VIDU')}/start-end2video"
        logger.info(
            "[Vidu] Submitting start-end2video task (model=%s, duration=%ss)",
            used_model, duration,
        )

        resp = requests.post(submit_url, headers=self._headers(), json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Vidu startend2video submission failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in Vidu response: {data}")

        return task_id, used_model
