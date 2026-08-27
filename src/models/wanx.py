import base64
import mimetypes
import os
import time
import requests
from http import HTTPStatus
from dashscope import VideoSynthesis
import dashscope
from .base import VideoGenModel
from ..utils import get_logger
from ..utils.endpoints import get_provider_base_url

from typing import Callable, Dict, List, Mapping, Optional, Tuple

from ..utils.oss_utils import OSSImageUploader
from ..utils.provider_media import resolve_media_input, resolve_media_inputs
from ..utils.provider_registry import resolve_provider_backend

logger = get_logger(__name__)


class WanxModel(VideoGenModel):
    def __init__(self, config):
        super().__init__(config)

        self.params = config.get('params', {})

    @property
    def api_key(self):
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("Dashscope API Key not found in config or environment variables.")
        return api_key

    def _resolve_provider_backend_for_model(self, model_name: str) -> str:
        try:
            return resolve_provider_backend(model_name)
        except (KeyError, ValueError):
            logger.debug(
                "Provider backend not registered for model %s, defaulting to dashscope.",
                model_name,
            )
            return "dashscope"
        except Exception as e:
            logger.warning(
                "Unexpected error resolving provider backend for model %s: %s. "
                "Falling back to dashscope.",
                model_name,
                e,
            )
            return "dashscope"

    def _resolver_model_for_media(self, model_name: str) -> str:
        # `wan2.5-i2v` follows the same DashScope media transport profile as `wan2.6-i2v`.
        if (model_name or "").strip().lower() == "wan2.5-i2v":
            return "wan2.6-i2v"
        # Wan2.7 I2V follows the same media transport profile as Wan2.6 I2V.
        if (model_name or "").strip().lower() == "wan2.7-i2v":
            return "wan2.6-i2v"
        # Wan2.7 R2V follows the same media transport profile as Wan2.6 R2V.
        if (model_name or "").strip().lower() == "wan2.7-r2v":
            return "wan2.6-r2v"
        return model_name

    def _build_dashscope_temp_url_resolver(self, model_name: str) -> Callable[[str], str]:
        return lambda local_path: self._create_dashscope_temp_url(local_path, model_name)

    @staticmethod
    def _merge_media_headers(target: Dict[str, str], source: Optional[Mapping[str, str]]) -> None:
        if not source:
            return
        for key, value in source.items():
            if value:
                target[key] = value

    def _encode_local_image_as_data_uri(self, local_path: str) -> str:
        mime_type, _ = mimetypes.guess_type(local_path)
        if not mime_type:
            mime_type = "image/png"
        with open(local_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _resolve_sdk_image_input(
        self,
        *,
        model_name: str,
        img_path: Optional[str],
        img_url: Optional[str],
        uploader,
    ) -> Optional[str]:
        """
        Resolve image input for SDK-based I2V calls.

        This keeps DashScope provider-mode routing consistent for non-Wan model
        names (e.g. Kling/Vidu via DashScope) and prevents raw local filesystem
        paths from leaking into SDK payloads.
        """
        image_ref = img_path or img_url
        if not image_ref:
            return img_url

        resolver_model = self._resolver_model_for_media(model_name)
        backend = self._resolve_provider_backend_for_model(resolver_model)
        temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

        try:
            resolved_image = resolve_media_input(
                image_ref,
                model_name=resolver_model,
                modality="image",
                backend=backend,
                uploader=uploader,
                dashscope_temp_url_resolver=temp_url_resolver,
            )
            if resolved_image.headers:
                logger.warning(
                    "SDK path for model %s received additional media headers %s; "
                    "continuing with resolved image value only.",
                    model_name,
                    list(resolved_image.headers.keys()),
                )
            return resolved_image.value
        except Exception as e:
            logger.warning(
                "Failed to resolve SDK image input via provider-media for model %s: %s. "
                "Falling back to raw image reference handling.",
                model_name,
                e,
            )

        local_candidate = None
        if img_path and os.path.exists(img_path):
            local_candidate = img_path
        elif isinstance(img_url, str) and os.path.exists(img_url):
            local_candidate = img_url

        if local_candidate:
            return self._encode_local_image_as_data_uri(local_candidate)
        return img_url

    def _create_dashscope_temp_url(self, local_path: str, model_name: str) -> str:
        """
        Upload a local file to DashScope temporary storage and return an `oss://` URL.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local media file not found: {local_path}")

        base = get_provider_base_url("DASHSCOPE")
        policy_url = f"{base}/api/v1/uploads"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        policy_resp = requests.get(
            policy_url,
            params={"action": "getPolicy", "model": model_name},
            headers=headers,
            timeout=30,
        )
        if policy_resp.status_code != 200:
            raise RuntimeError(
                f"Failed to get DashScope upload policy (HTTP {policy_resp.status_code}): "
                f"{policy_resp.text}"
            )

        policy_body = policy_resp.json()
        policy_data = policy_body.get("output") or policy_body.get("data") or policy_body

        upload_host = policy_data.get("upload_host") or policy_data.get("host")
        if not upload_host:
            raise RuntimeError(f"DashScope upload policy missing upload_host: {policy_body}")

        upload_dir = policy_data.get("upload_dir") or policy_data.get("dir") or ""
        object_key = (
            policy_data.get("upload_file_path")
            or policy_data.get("object_key")
            or policy_data.get("key")
            or policy_data.get("file_path")
        )
        if not object_key:
            filename = os.path.basename(local_path)
            object_key = f"{upload_dir.rstrip('/')}/{filename}" if upload_dir else filename

        form_data: Dict[str, str] = {"key": object_key}
        field_map = {
            "policy": "policy",
            "signature": "signature",
            "oss_access_key_id": "OSSAccessKeyId",
            "x_oss_security_token": "x-oss-security-token",
            "x_oss_signature_version": "x-oss-signature-version",
            "x_oss_credential": "x-oss-credential",
            "x_oss_date": "x-oss-date",
            "x_oss_signature": "x-oss-signature",
            "x_oss_object_acl": "x-oss-object-acl",
            "x_oss_forbid_overwrite": "x-oss-forbid-overwrite",
            "success_action_status": "success_action_status",
            "callback": "callback",
        }
        for source_key, target_key in field_map.items():
            value = policy_data.get(source_key)
            if value:
                form_data[target_key] = str(value)

        # DashScope OSS policy requires x-oss-object-acl=private and
        # x-oss-forbid-overwrite=true; ensure they are always present
        # even if the policy response omits them (Bucket Policy enforcement)
        if "x-oss-object-acl" not in form_data:
            form_data["x-oss-object-acl"] = "private"
        if "x-oss-forbid-overwrite" not in form_data:
            form_data["x-oss-forbid-overwrite"] = "true"

        for key, value in policy_data.items():
            if key.startswith("x-oss-") and value and key not in form_data:
                form_data[key] = str(value)

        with open(local_path, "rb") as file_handle:
            files = {"file": (os.path.basename(local_path), file_handle)}
            upload_resp = requests.post(upload_host, data=form_data, files=files, timeout=120)

        if upload_resp.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Failed to upload temp media to DashScope (HTTP {upload_resp.status_code}): "
                f"{upload_resp.text}"
            )

        return f"oss://{object_key}"

    def generate(self, prompt: str, output_path: str, img_path: str = None, model_name: str = None, **kwargs) ->Tuple[str, float]:
        # Determine model - allow explicit override via model_name param or 'model' kwarg
        # Fix: pipeline.py passes 'model=task.model', we need to accept both
        if model_name:
            final_model_name = model_name
        elif kwargs.get('model'):
            final_model_name = kwargs.get('model')
            logger.info(f"Using model from kwargs: {final_model_name}")
        elif img_path or kwargs.get('img_url'):
            final_model_name = self.params.get('i2v_model_name', 'wan2.7-i2v')  # Default to I2V model
            logger.info(f"Using I2V model: {final_model_name}")
        else:
            final_model_name = self.params.get('model_name', 'wan2.7-i2v')
            logger.info(f"Using T2V model: {final_model_name}")

        size = self.params.get('size', '1280*720')
        prompt_extend = kwargs.get('prompt_extend', self.params.get('prompt_extend', True))
        watermark = kwargs.get('watermark', self.params.get('watermark', False))

        # New parameters - prioritize kwargs, fallback to params
        duration = kwargs.get('duration') or self.params.get('duration', 5)
        negative_prompt = kwargs.get('negative_prompt') or self.params.get('negative_prompt', '')
        audio_url = kwargs.get('audio_url') or self.params.get('audio_url', '')
        seed = kwargs.get('seed') or self.params.get('seed')

        # Resolution mapping - normalize to uppercase for API
        resolution = kwargs.get('resolution') or self.params.get('resolution', '720P')
        resolution = resolution.upper()  # API requires uppercase (720P, 1080P)
        if resolution == '1080P':
            size = "1920*1080"
        elif resolution == '480P':
            size = "832*480"
        else:
            size = "1280*720"

        # Motion params
        camera_motion = kwargs.get('camera_motion')
        subject_motion = kwargs.get('subject_motion')

        logger.info(f"Starting generation with model: {final_model_name}")
        logger.info(f"Prompt: {prompt}")

        try:
            api_start_time = time.time()

            img_url = kwargs.get('img_url')
            uploader = OSSImageUploader()
            extra_media_headers: Dict[str, str] = {}

            # Use HTTP API for wan2.7-i2v, wan2.6-i2v, wan2.5-i2v, or wan2.6-r2v
            if final_model_name in ['wan2.7-i2v', 'wan2.6-i2v', 'wan2.6-i2v-flash', 'wan2.5-i2v']:
                resolver_model = self._resolver_model_for_media(final_model_name)
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                image_ref = img_path or img_url
                if image_ref:
                    resolved_image = resolve_media_input(
                        image_ref,
                        model_name=resolver_model,
                        modality="image",
                        backend=backend,
                        uploader=uploader,
                        dashscope_temp_url_resolver=temp_url_resolver,
                    )
                    # For Wan I2V, keep local no-OSS image inputs URL-based just like audio/video
                    # (oss:// + X-DashScope-OssResourceResolve), not data URIs.
                    if resolved_image.value.startswith("data:image/"):
                        resolved_image = resolve_media_input(
                            image_ref,
                            model_name=resolver_model,
                            modality="reference_video",
                            backend=backend,
                            uploader=uploader,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )

                    img_url = resolved_image.value
                    self._merge_media_headers(extra_media_headers, resolved_image.headers)

                if audio_url:
                    resolved_audio = resolve_media_input(
                        audio_url,
                        model_name=resolver_model,
                        modality="audio",
                        backend=backend,
                        uploader=uploader,
                        dashscope_temp_url_resolver=temp_url_resolver,
                    )
                    audio_url = resolved_audio.value
                    self._merge_media_headers(extra_media_headers, resolved_audio.headers)

                # Get shot_type from kwargs (only for wan I2V models)
                shot_type = kwargs.get('shot_type', 'single')
                # Wan2.7 uses ratio instead of resolution
                ratio = kwargs.get('ratio')
                is_wan27_i2v = final_model_name.startswith('wan2.7-')
                video_url = self._generate_wan_i2v_http(
                    prompt=prompt,
                    img_url=img_url,
                    model_name=final_model_name,
                    resolution=resolution if not is_wan27_i2v else None,
                    ratio=ratio if is_wan27_i2v else None,
                    duration=duration,
                    prompt_extend=prompt_extend,
                    negative_prompt=negative_prompt,
                    audio_url=audio_url,
                    watermark=watermark,
                    seed=seed,
                    shot_type=shot_type,
                    extra_headers=extra_media_headers,
                )
            elif final_model_name in ('wan2.6-r2v', 'wan2.7-r2v'):
                # R2V generation
                is_wan27_r2v = final_model_name == 'wan2.7-r2v'
                ref_key = 'ref_image_urls' if is_wan27_r2v else 'ref_video_urls'
                ref_urls = kwargs.get(ref_key, [])
                if not ref_urls:
                    raise ValueError(f"{ref_key} is required for {final_model_name}")

                resolver_model = self._resolver_model_for_media(final_model_name)
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                modality = "image" if is_wan27_r2v else "reference_video"
                resolved_ref_urls = resolve_media_inputs(
                    ref_urls,
                    model_name=resolver_model,
                    modality=modality,
                    backend=backend,
                    uploader=uploader,
                    dashscope_temp_url_resolver=temp_url_resolver,
                )
                ref_urls_resolved = [item.value for item in resolved_ref_urls]
                for resolved_item in resolved_ref_urls:
                    self._merge_media_headers(extra_media_headers, resolved_item.headers)

                shot_type = kwargs.get('shot_type', 'multi') # Default to multi for R2V as per PRD
                ratio = kwargs.get('ratio')

                video_url = self._generate_wan_r2v_http(
                    prompt=prompt,
                    ref_video_urls=ref_urls_resolved,
                    model_name=final_model_name,
                    size=size if not is_wan27_r2v else None,
                    ratio=ratio if is_wan27_r2v else None,
                    duration=duration,
                    audio=kwargs.get('audio', True), # Default to True for R2V
                    shot_type=shot_type,
                    seed=seed,
                    extra_headers=extra_media_headers,
                )
            elif final_model_name in ('wan2.7-t2v', 'wan2.7-videoedit'):
                # Wan2.7 T2V and VideoEdit via HTTP API
                ratio = kwargs.get('ratio')
                media = None
                resolver_model = self._resolver_model_for_media(final_model_name)
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                if final_model_name == 'wan2.7-videoedit':
                    # VideoEdit: video + optional reference images
                    video_input = kwargs.get('video_url')
                    if video_input:
                        resolved_video = resolve_media_input(
                            video_input,
                            model_name=resolver_model,
                            modality="reference_video",
                            backend=backend,
                            uploader=uploader,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        media = [{"type": "video", "url": resolved_video.value}]
                        self._merge_media_headers(extra_media_headers, resolved_video.headers)
                    ref_image_urls = kwargs.get('ref_image_urls', [])
                    if ref_image_urls:
                        resolved_imgs = resolve_media_inputs(
                            ref_image_urls,
                            model_name=resolver_model,
                            modality="image",
                            backend=backend,
                            uploader=uploader,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        for r in resolved_imgs:
                            media.append({"type": "reference_image", "url": r.value})
                            self._merge_media_headers(extra_media_headers, r.headers)

                video_url = self._generate_hh_http(
                    prompt=prompt,
                    model_name=final_model_name,
                    media=media,
                    duration=duration,
                    ratio=ratio,
                    seed=seed,
                    watermark=watermark,
                    extra_headers=extra_media_headers,
                )
            elif final_model_name.startswith('happyhorse-1.0-'):
                # HappyHorse model family (I2V, R2V, T2V, V2V)
                resolver_model = final_model_name
                backend = self._resolve_provider_backend_for_model(resolver_model)
                temp_url_resolver = self._build_dashscope_temp_url_resolver(resolver_model)

                # Build media array based on model type
                media = None
                if final_model_name == 'happyhorse-1.0-i2v':
                    # I2V: first_frame image
                    image_ref = img_path or img_url
                    if image_ref:
                        resolved = resolve_media_input(
                            image_ref,
                            model_name=resolver_model,
                            modality="image",
                            backend=backend,
                            uploader=uploader,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        media = [{"type": "first_frame", "url": resolved.value}]
                        self._merge_media_headers(extra_media_headers, resolved.headers)

                elif final_model_name == 'happyhorse-1.0-r2v':
                    # R2V: reference images (1-9)
                    ref_image_urls = kwargs.get('ref_image_urls', [])
                    if not ref_image_urls:
                        raise ValueError("ref_image_urls is required for happyhorse-1.0-r2v")
                    resolved_refs = resolve_media_inputs(
                        ref_image_urls,
                        model_name=resolver_model,
                        modality="image",
                        backend=backend,
                        uploader=uploader,
                        dashscope_temp_url_resolver=temp_url_resolver,
                    )
                    media = [{"type": "reference_image", "url": r.value} for r in resolved_refs]
                    for r in resolved_refs:
                        self._merge_media_headers(extra_media_headers, r.headers)

                elif final_model_name == 'happyhorse-1.0-video-edit':
                    # V2V: video + optional reference images (reserved for future)
                    ref_image_urls = kwargs.get('ref_image_urls', [])
                    video_input = kwargs.get('video_url')
                    if video_input:
                        resolved_video = resolve_media_input(
                            video_input,
                            model_name=resolver_model,
                            modality="reference_video",
                            backend=backend,
                            uploader=uploader,
                            dashscope_temp_url_resolver=temp_url_resolver,
                        )
                        media = [{"type": "video", "url": resolved_video.value}]
                        self._merge_media_headers(extra_media_headers, resolved_video.headers)
                        # Add reference images if provided
                        if ref_image_urls:
                            resolved_imgs = resolve_media_inputs(
                                ref_image_urls,
                                model_name=resolver_model,
                                modality="image",
                                backend=backend,
                                uploader=uploader,
                                dashscope_temp_url_resolver=temp_url_resolver,
                            )
                            for r in resolved_imgs:
                                media.append({"type": "reference_image", "url": r.value})
                                self._merge_media_headers(extra_media_headers, r.headers)
                # T2V: no media needed

                video_url = self._generate_hh_http(
                    prompt=prompt,
                    model_name=final_model_name,
                    media=media,
                    resolution=resolution,
                    duration=duration,
                    ratio=kwargs.get('ratio'),
                    seed=seed,
                    watermark=watermark,
                    audio_setting=kwargs.get('audio_setting'),
                    extra_headers=extra_media_headers,
                    on_provider_ids=kwargs.get('on_provider_ids'),
                )
            else:
                # Use SDK for other models
                if img_path or img_url:
                    img_url = self._resolve_sdk_image_input(
                        model_name=final_model_name,
                        img_path=img_path,
                        img_url=img_url,
                        uploader=uploader,
                    )
                video_url = self._generate_sdk(
                    prompt=prompt,
                    model_name=final_model_name,
                    img_url=img_url,
                    size=size,
                    duration=duration,
                    prompt_extend=prompt_extend,
                    negative_prompt=negative_prompt,
                    audio_url=audio_url,
                    watermark=watermark,
                    seed=seed,
                    camera_motion=camera_motion,
                    subject_motion=subject_motion
                )

            api_end_time = time.time()
            api_duration = api_end_time - api_start_time

            logger.info(f"Generation success. Video URL: {video_url}")
            logger.info(f"API duration: {api_duration:.2f}s")

            # Download video
            self._download_video(video_url, output_path)
            return output_path, api_duration

        except Exception as e:
            logger.error(f"Error during generation: {e}")
            raise

    def _generate_wan_i2v_http(self, prompt: str, img_url: str, model_name: str = "wan2.6-i2v",
                                  resolution: str = "720P", ratio: Optional[str] = None,
                                  duration: int = 5, prompt_extend: bool = True,
                                  negative_prompt: str = None, audio_url: str = None,
                                  watermark: bool = False, seed: int = None,
                                  shot_type: str = "single",
                                  extra_headers: Optional[Mapping[str, str]] = None) -> str:
        """Generate video using Wan I2V (2.5/2.6/2.7) via HTTP API (asynchronous with polling)."""
        base = get_provider_base_url("DASHSCOPE")
        create_url = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"  # Required for async mode
        }
        if extra_headers:
            headers.update(dict(extra_headers))
        
        payload = {
            "model": model_name,  # Use passed model name (wan2.5-i2v, wan2.6-i2v, or wan2.7-i2v)
            "input": {
                "prompt": prompt,
                "img_url": img_url
            },
            "parameters": {
                "duration": duration,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
                "audio": True,  # Auto-generate audio
                "shot_type": shot_type  # single or multi (only works when prompt_extend=True)
            }
        }

        # Wan2.7 uses ratio; older models use resolution
        if ratio:
            payload["parameters"]["ratio"] = ratio
        elif resolution:
            payload["parameters"]["resolution"] = resolution
        
        # Add optional parameters
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt
        if audio_url:
            payload["input"]["audio_url"] = audio_url
            del payload["parameters"]["audio"]  # audio_url takes precedence
        if seed:
            payload["parameters"]["seed"] = seed
        
        logger.info(f"Calling {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")
        
        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)  # 2 minutes for task creation
        
        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500] if response.text else 'empty'}")
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"{model_name} task creation failed: {error_msg}")
        
        result = response.json()
        task_id = result.get('output', {}).get('task_id')
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")
        
        logger.info(f"Task created: {task_id}")
        
        # Step 2: Poll for task completion
        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        max_wait_time = 900  # 15 minutes max wait (video generation takes longer)
        poll_interval = 15   # Poll every 15 seconds
        elapsed = 0
        
        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval
            
            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)
            
            if poll_response.status_code != 200:
                logger.warning(f"Poll request failed: {poll_response.status_code}")
                continue
            
            poll_result = poll_response.json()
            task_status = poll_result.get('output', {}).get('task_status')
            
            logger.info(f"Task {task_id} status: {task_status} (elapsed: {elapsed}s)")
            
            if task_status == 'SUCCEEDED':
                video_url = poll_result.get('output', {}).get('video_url')
                if not video_url:
                    raise RuntimeError(f"No video_url in completed task: {poll_result}")
                
                logger.info(f"Task completed. Video URL: {video_url}")
                return video_url
            
            elif task_status == 'FAILED':
                error_msg = poll_result.get('output', {}).get('message', 'Unknown error')
                code = poll_result.get('output', {}).get('code', '')
                raise RuntimeError(f"{model_name} task failed: {code} - {error_msg}")
            
            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")
            
            # PENDING or RUNNING - continue polling
        
        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_wan_r2v_http(self, prompt: str, ref_video_urls: list, model_name: str = "wan2.6-r2v",
                                  size: Optional[str] = "1280*720", ratio: Optional[str] = None,
                                  duration: int = 5, audio: bool = True,
                                  shot_type: str = "multi", seed: int = None,
                                  extra_headers: Optional[Mapping[str, str]] = None) -> str:
        """Generate video using Wan R2V (2.6/2.7) via HTTP API (asynchronous with polling)."""
        base = get_provider_base_url("DASHSCOPE")
        create_url = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"
        }
        if extra_headers:
            headers.update(dict(extra_headers))

        # wan2.7 R2V uses the unified DashScope media format: input.media is a
        # list of typed {"type", "url"} objects (reference sheets map to
        # "reference_image"). The legacy wan2.6 protocol takes a plain string
        # array under input.reference_urls. The old field names
        # ("reference_image_urls" / "reference_video_urls") are accepted by
        # neither and fail async validation with "Field required: input.media".
        input_payload = {"prompt": prompt}
        if model_name.startswith("wan2.7-"):
            input_payload["media"] = [
                {"type": "reference_image", "url": url} for url in ref_video_urls
            ]
        else:
            input_payload["reference_urls"] = list(ref_video_urls)

        payload = {
            "model": model_name,
            "input": input_payload,
            "parameters": {
                "duration": duration,
                "audio": audio,
                "shot_type": shot_type
            }
        }

        # Wan2.7 uses ratio; older models use size
        if ratio:
            payload["parameters"]["ratio"] = ratio
        elif size:
            payload["parameters"]["size"] = size
        
        if seed:
            payload["parameters"]["seed"] = seed
        
        logger.info(f"Calling {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")
        
        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)
        
        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500] if response.text else 'empty'}")
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"{model_name} task creation failed: {error_msg}")
        
        result = response.json()
        task_id = result.get('output', {}).get('task_id')
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")
        
        logger.info(f"Task created: {task_id}")
        
        # Step 2: Poll for task completion
        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        max_wait_time = 900  # 15 minutes max wait
        poll_interval = 15   # Poll every 15 seconds
        elapsed = 0
        
        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval
            
            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)
            
            if poll_response.status_code != 200:
                logger.warning(f"Poll request failed: {poll_response.status_code}")
                continue
            
            poll_result = poll_response.json()
            task_status = poll_result.get('output', {}).get('task_status')
            
            logger.info(f"Task {task_id} status: {task_status} (elapsed: {elapsed}s)")
            
            if task_status == 'SUCCEEDED':
                video_url = poll_result.get('output', {}).get('video_url')
                if not video_url:
                    raise RuntimeError(f"No video_url in completed task: {poll_result}")
                
                logger.info(f"Task completed. Video URL: {video_url}")
                return video_url
            
            elif task_status == 'FAILED':
                error_msg = poll_result.get('output', {}).get('message', 'Unknown error')
                code = poll_result.get('output', {}).get('code', '')
                raise RuntimeError(f"{model_name} task failed: {code} - {error_msg}")
            
            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")
            
        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_hh_http(self, *, prompt: str, model_name: str,
                           media: Optional[List[Dict[str, str]]] = None,
                           resolution: str = "1080P", duration: int = 5,
                           ratio: Optional[str] = None, seed: Optional[int] = None,
                           watermark: bool = False, audio_setting: Optional[str] = None,
                           extra_headers: Optional[Mapping[str, str]] = None,
                           on_provider_ids: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None) -> str:
        """Generate video using HappyHorse models via HTTP API (asynchronous with polling).

        on_provider_ids: optional callback fired AS SOON AS task creation succeeds,
        BEFORE the long polling loop begins. Signature is
        `(provider_name, provider_task_id, provider_request_id)`. Used by the
        pipeline to persist Bailian / DashScope IDs onto our VideoTask so the
        user can paste them into the 百炼 console for diagnosis even while the
        task is still running (Issue 17). Failures during the callback are
        logged but do not interrupt generation.
        """
        """Generate video using HappyHorse models via HTTP API (asynchronous with polling)."""
        base = get_provider_base_url("DASHSCOPE")
        create_url = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"
        }
        if extra_headers:
            headers.update(dict(extra_headers))

        payload = {
            "model": model_name,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "resolution": resolution,
                "duration": duration,
                "watermark": watermark,
            }
        }

        # Add media array if provided (I2V/R2V/V2V)
        if media:
            payload["input"]["media"] = media

        # Model-specific parameters
        if ratio and model_name != "happyhorse-1.0-i2v":
            # I2V doesn't support ratio parameter
            payload["parameters"]["ratio"] = ratio
        if seed:
            payload["parameters"]["seed"] = seed
        if audio_setting and model_name == "happyhorse-1.0-video-edit":
            payload["parameters"]["audio_setting"] = audio_setting

        logger.info(f"Calling HappyHorse {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")

        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)

        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500] if response.text else 'empty'}")

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"{model_name} task creation failed: {error_msg}")

        result = response.json()
        task_id = result.get('output', {}).get('task_id')
        request_id = result.get('request_id')
        if not task_id:
            raise RuntimeError(f"No task_id in response: {result}")

        logger.info(f"HappyHorse task created: {task_id} (request_id={request_id})")

        # Fire provider-id callback ASAP — pipeline persists these onto our
        # VideoTask so the UI / user can copy them while the task is still
        # running. Wrap in try/except so a buggy callback can't kill the
        # whole generation flow.
        if on_provider_ids is not None:
            try:
                on_provider_ids("dashscope", task_id, request_id)
            except Exception as cb_err:
                logger.warning(f"on_provider_ids callback failed: {cb_err}")

        # Step 2: Poll for task completion
        poll_url = f"{base}/api/v1/tasks/{task_id}"
        poll_headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        max_wait_time = 900  # 15 minutes max wait
        poll_interval = 15   # Poll every 15 seconds
        elapsed = 0

        while elapsed < max_wait_time:
            time.sleep(poll_interval)
            elapsed += poll_interval

            poll_response = requests.get(poll_url, headers=poll_headers, timeout=30)

            if poll_response.status_code != 200:
                logger.warning(f"Poll request failed: {poll_response.status_code}")
                continue

            poll_result = poll_response.json()
            task_status = poll_result.get('output', {}).get('task_status')

            logger.info(f"Task {task_id} status: {task_status} (elapsed: {elapsed}s)")

            if task_status == 'SUCCEEDED':
                video_url = poll_result.get('output', {}).get('video_url')
                if not video_url:
                    raise RuntimeError(f"No video_url in completed task: {poll_result}")

                logger.info(f"HappyHorse task completed. Video URL: {video_url}")
                return video_url

            elif task_status == 'FAILED':
                error_msg = poll_result.get('output', {}).get('message', 'Unknown error')
                code = poll_result.get('output', {}).get('code', '')
                raise RuntimeError(f"{model_name} task failed: {code} - {error_msg}")

            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")

            # PENDING or RUNNING - continue polling

        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_sdk(self, prompt: str, model_name: str, img_url: str = None, size: str = "1280*720",
                      duration: int = 5, prompt_extend: bool = True, negative_prompt: str = None,
                      audio_url: str = None, watermark: bool = False, seed: int = None,
                      camera_motion: str = None, subject_motion: str = None) -> str:
        """Generate video using Dashscope SDK (for older models)."""
        # Prepare arguments
        call_args = {
            "api_key": self.api_key,
            "model": model_name,
            "prompt": prompt,
            "size": size,
            "prompt_extend": prompt_extend,
            "watermark": watermark,
        }
        
        # Add optional arguments if they exist
        if negative_prompt:
            call_args['negative_prompt'] = negative_prompt
        if duration:
            call_args['duration'] = duration
        if audio_url:
            call_args['audio_url'] = audio_url
        if seed:
            call_args['seed'] = seed
        if camera_motion:
            call_args['camera_motion'] = camera_motion
        if subject_motion:
            call_args['motion_scale'] = subject_motion
        
        if img_url:
            call_args['img_url'] = img_url
            logger.info(f"Image to Video mode. Input Image URL: {img_url}")

        rsp = VideoSynthesis.async_call(**call_args)
        
        if rsp.status_code != HTTPStatus.OK:
            logger.error(f"Failed to submit task: {rsp.code}, {rsp.message}")
            raise RuntimeError(f"Task submission failed: {rsp.message}")
        
        task_id = rsp.output.task_id
        logger.info(f"Task submitted. Task ID: {task_id}")
        
        # Wait for completion
        rsp = VideoSynthesis.wait(rsp)
        
        logger.info(f"SDK response: {rsp}")

        if rsp.status_code != HTTPStatus.OK:
            logger.error(f"Task failed with status code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}")
            raise RuntimeError(f"Task failed: {rsp.message}")
        
        if rsp.output.task_status != 'SUCCEEDED':
             logger.error(f"Task finished but status is {rsp.output.task_status}. Code: {rsp.output.code}, Message: {rsp.output.message}")
             raise RuntimeError(f"Task failed with status {rsp.output.task_status}: {rsp.output.message}")

        video_url = rsp.output.video_url
        if not video_url:
             logger.error("Video URL is empty despite SUCCEEDED status.")
             raise RuntimeError("Video URL is empty.")
        
        return video_url

    def _download_video(self, url: str, path: str):
        logger.info(f"Downloading video to {path}...")

        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry

        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        temp_path = path + ".tmp"
        try:
            response = session.get(url, stream=True, timeout=120)  # 2 minutes for large video files
            response.raise_for_status()

            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Atomic rename
            os.rename(temp_path, path)
            logger.info("Download complete.")

        except Exception as e:
            logger.error(f"Failed to download video: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
