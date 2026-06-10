from abc import ABC, abstractmethod
from contextlib import ExitStack
from pathlib import Path
from typing import Dict, Any, List, Mapping, Optional, Tuple
import base64
import mimetypes
import os
import tempfile
import time
from urllib.parse import urlparse
import requests
from http import HTTPStatus
import dashscope
from dashscope import ImageSynthesis
from ..utils import get_logger
from ..utils.endpoints import get_provider_base_url
from ..utils.media_refs import MEDIA_REF_UNKNOWN, classify_media_ref
from ..utils.oss_utils import OSSImageUploader, is_object_key
from ..utils.provider_media import resolve_media_input
from ..utils.provider_registry import resolve_provider_backend

logger = get_logger(__name__)


def _parse_image_size(size: str) -> Optional[Tuple[int, int]]:
    normalized = (size or "").replace("*", "x").strip().lower()
    parts = normalized.split("x")
    if len(parts) != 2:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _is_valid_gpt_image_2_size(width: int, height: int) -> bool:
    max_edge = max(width, height)
    min_edge = min(width, height)
    total_pixels = width * height
    return (
        max_edge <= 3840
        and width % 16 == 0
        and height % 16 == 0
        and max_edge / min_edge <= 3.0
        and 655_360 <= total_pixels <= 8_294_400
    )


def _orientation_size_fallback(width: int, height: int) -> str:
    if width > height:
        return "1536x1024"
    if height > width:
        return "1024x1536"
    return "1024x1024"


def _normalize_openai_image_size(size: str, model_name: str) -> str:
    normalized = (
        str(size or "1024x1024")
        .replace("*", "x")
        .strip()
        .lower()
    )
    if normalized == "auto":
        return normalized

    parsed = _parse_image_size(normalized)
    if not parsed:
        return "1024x1024"

    width, height = parsed
    if model_name == "gpt-image-2":
        if _is_valid_gpt_image_2_size(width, height):
            return normalized
        return _orientation_size_fallback(width, height)

    if normalized in {"1024x1024", "1536x1024", "1024x1536", "auto"}:
        return normalized
    return _orientation_size_fallback(width, height)


def _normalize_openai_output_format(output_format: Optional[str], output_path: str) -> str:
    if output_format:
        fmt = output_format.strip().lower()
    else:
        fmt = Path(output_path).suffix.lstrip(".").lower() or 'webp'
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in {"png", "jpeg", "webp"}:
        return 'jpeg'
    return fmt


def _media_suffix_from_mime(mime_type: Optional[str]) -> str:
    if not mime_type:
        return ".png"
    suffix = mimetypes.guess_extension(mime_type.split(";", 1)[0].strip())
    return suffix or ".png"

class ImageGenModel(ABC):
    """Abstract base class for image generation models."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        """
        Generates an image from a prompt.
        
        Args:
            prompt: The input text prompt.
            output_path: The path to save the generated image.
            **kwargs: Additional arguments.
            
        Returns:
            A tuple containing:
            - The path to the generated image file.
            - The duration of the API generation process in seconds.
        """
        pass

class WanxImageModel(ImageGenModel):
    def __init__(self, config):
        super().__init__(config)
        self.params = config.get('params', {})

    @property
    def api_key(self):
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("Dashscope API Key not found in config or environment variables.")
        return api_key

    def generate(self, prompt: str, output_path: str, ref_image_path: str = None, ref_image_paths: list = None, model_name: str = None, **kwargs) -> Tuple[str, float]:
        # Determine model based on whether reference image is provided
        # Support both single path (legacy) and list of paths
        dashscope.api_key = self.api_key

        all_ref_paths = []
        if ref_image_path:
            all_ref_paths.append(ref_image_path)
        if ref_image_paths:
            all_ref_paths.extend(ref_image_paths)
            
        # Remove duplicates
        all_ref_paths = list(set(all_ref_paths))
        # Model selection priority: explicit model_name > config params > defaults
        if model_name:
            final_model_name = model_name
        elif all_ref_paths:
            # For I2I, use i2i_model_name if configured, otherwise default to wan2.7-image
            final_model_name = self.params.get('i2i_model_name', 'wan2.7-image')
        else:
            # For T2I, use model_name if configured, otherwise default to wan2.7-image-pro
            final_model_name = self.params.get('model_name', 'wan2.7-image-pro')

        if all_ref_paths:
            logger.info(f"Using I2I model: {final_model_name} with {len(all_ref_paths)} reference images")
        else:
            logger.info(f"Using T2I model: {final_model_name}")

        size = kwargs.pop('size', self.params.get('size', '1280*1280'))
        n = kwargs.pop('n', self.params.get('n', 1))
        negative_prompt = kwargs.pop('negative_prompt', None)
        # model_name is already handled above, remove from kwargs if present
        kwargs.pop('model_name', None)
        
        # Determine reference image limit based on model
        if final_model_name.startswith('wan2.7-image') or final_model_name.startswith('qwen-image'):
            ref_limit = 9
        elif final_model_name == 'wan2.6-image':
            ref_limit = 4
        else:
            ref_limit = 3
        if len(all_ref_paths) > ref_limit:
            logger.warning(f"Limiting reference images from {len(all_ref_paths)} to {ref_limit} for model {final_model_name}")
            all_ref_paths = all_ref_paths[:ref_limit]
        
        logger.info(f"Starting image generation...")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Model: {final_model_name}, Size: {size}, N: {n}")

        try:
            api_start_time = time.time()
            # Use HTTP API for wan2.6+ models and qwen-image models
            if final_model_name == 'wan2.6-t2i':
                image_url = self._generate_wan26_http(prompt, size, n, negative_prompt)
            elif final_model_name == 'wan2.6-image':
                # wan2.6-image for I2I (requires reference images)
                image_url = self._generate_wan26_image_http(prompt, size, n, negative_prompt, all_ref_paths)
            elif final_model_name.startswith('wan2.7-image') or final_model_name.startswith('qwen-image'):
                # Wan2.7-image / Qwen-image via DashScope async HTTP API
                image_url = self._generate_dashscope_image_http(
                    prompt=prompt,
                    model_name=final_model_name,
                    size=size,
                    n=n,
                    negative_prompt=negative_prompt,
                    ref_image_paths=all_ref_paths,
                    seed=kwargs.pop('seed', None),
                    prompt_extend=kwargs.pop('prompt_extend', True),
                    watermark=kwargs.pop('watermark', False),
                )
            else:
                # Use SDK for other models
                image_url = self._generate_sdk(prompt, final_model_name, size, n, negative_prompt, all_ref_paths,
                                               kwargs)

            api_end_time = time.time()
            api_duration = api_end_time - api_start_time

            logger.info(f"Generation success. Image URL: {image_url}")
            logger.info(f"API duration: {api_duration:.2f}s")
            
            # Download image
            self._download_image(image_url, output_path)
            return output_path, api_duration

        except Exception as e:
            import traceback
            logger.error(f"Error during generation: {e}")
            logger.error(traceback.format_exc())
            raise

    def _generate_dashscope_image_http(
        self,
        prompt: str,
        model_name: str,
        size: str = "1280*1280",
        n: int = 1,
        negative_prompt: str = None,
        ref_image_paths: list = None,
        seed: int = None,
        prompt_extend: bool = True,
        watermark: bool = False,
    ) -> str:
        """Generate image using Wan2.7-image / Qwen-image via DashScope async HTTP API."""
        base = get_provider_base_url("DASHSCOPE")
        create_url = f"{base}/api/v1/services/aigc/image-generation/generation"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable",
        }

        # Build content array with optional reference images and prompt text
        content = []
        if ref_image_paths:
            for path in ref_image_paths[:9]:
                image_input = self._resolve_wan26_reference_image(path, model_name=model_name)
                if image_input:
                    content.append({"image": image_input})
        content.append({"text": prompt})

        payload = {
            "model": model_name,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ]
            },
            "parameters": {
                "prompt_extend": prompt_extend,
                "watermark": watermark,
                "n": n,
                "size": size,
            },
        }

        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt
        if seed:
            payload["parameters"]["seed"] = seed

        logger.info(f"Calling {model_name} HTTP API (async)...")
        logger.info(f"Payload: {payload}")

        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)

        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500]}")

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

        max_wait_time = 600
        poll_interval = 10
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
                choices = poll_result.get('output', {}).get('choices', [])
                if not choices:
                    raise RuntimeError(f"No choices in completed task: {poll_result}")

                first_choice = choices[0]
                content = first_choice.get('message', {}).get('content', [])
                if not content:
                    raise RuntimeError(f"No content in choice: {first_choice}")

                image_url = content[0].get('image')
                if not image_url:
                    raise RuntimeError(f"No image URL in content: {content}")

                logger.info(f"Task completed. Image URL: {image_url}")
                return image_url

            elif task_status == 'FAILED':
                error_msg = (
                    poll_result.get('output', {}).get('message', '') or
                    poll_result.get('output', {}).get('code', '') or
                    poll_result.get('message', '') or
                    poll_result.get('code', '') or
                    'Unknown error'
                )
                raise RuntimeError(f"{model_name} task failed: {error_msg}")

            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"{model_name} task {task_status}: {poll_result}")

        raise RuntimeError(f"{model_name} task timed out after {max_wait_time}s")

    def _generate_wan26_http(self, prompt: str, size: str, n: int, negative_prompt: str = None) -> str:
        """Generate image using Wan 2.6 T2I via HTTP API (synchronous)."""
        base = get_provider_base_url("DASHSCOPE")
        url = f"{base}/api/v1/services/aigc/multimodal-generation/generation"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": "wan2.6-t2i",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ]
            },
            "parameters": {
                "prompt_extend": False,  # Disable auto prompt rewriting for consistency
                "watermark": False,
                "n": n,
                "size": size
            }
        }
        
        # Add negative_prompt if provided
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt
        
        logger.info(f"Calling Wan 2.6 T2I HTTP API...")
        logger.info(f"Payload: {payload}")
        
        response = requests.post(url, headers=headers, json=payload, timeout=300)  # 5 minutes for slow API responses
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response body: {response.text[:500]}...")
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"Wan 2.6 API failed: {error_msg}")
        
        result = response.json()
        
        # Extract image URL from response
        # Response format: output.choices[].message.content[].image
        choices = result.get('output', {}).get('choices', [])
        if not choices:
            raise RuntimeError(f"No choices in response: {result}")
        
        # Get first image from first choice
        first_choice = choices[0]
        content = first_choice.get('message', {}).get('content', [])
        if not content:
            raise RuntimeError(f"No content in choice: {first_choice}")
        
        image_url = content[0].get('image')
        if not image_url:
            raise RuntimeError(f"No image URL in content: {content}")
        
        return image_url

    def _generate_wan26_image_http(self, prompt: str, size: str, n: int, negative_prompt: str = None, ref_image_paths: list = None) -> str:
        """Generate image using Wan 2.6 Image via HTTP API (asynchronous with polling)."""
        base = get_provider_base_url("DASHSCOPE")
        create_url = f"{base}/api/v1/services/aigc/image-generation/generation"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"  # Required for async mode
        }
        
        # Build content array with reference images and prompt text
        content = []
        
        # Add reference images (upload to OSS first if local paths)
        if ref_image_paths:
            # Limit is already handled in generate(), but we keep a safety slice here
            # This method is specifically for wan2.6-image which supports 4 images
            ref_limit = 4
            for path in ref_image_paths[:ref_limit]:
                image_input = self._resolve_wan26_reference_image(path)
                if image_input:
                    content.append({"image": image_input})

        if ref_image_paths and not content:
            raise RuntimeError(
                "Wan 2.6 Image requires at least one usable reference image. "
                "Please provide a valid local image, public URL, or configure OSS."
            )

        content.append({"text": prompt})
        
        payload = {
            "model": "wan2.6-image",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content
                    }
                ]
            },
            "parameters": {
                "prompt_extend": False,  # Disable auto prompt rewriting for consistency
                "watermark": False,
                "n": n,
                "size": size,
                "enable_interleave": False  # Image editing mode (I2I)
            }
        }
        
        # Add negative_prompt if provided
        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt
        
        logger.info(f"Calling Wan 2.6 Image HTTP API (async)...")
        logger.info(f"Payload: {payload}")
        
        # Step 1: Create task
        response = requests.post(create_url, headers=headers, json=payload, timeout=120)  # 2 minutes for task creation
        
        logger.info(f"Create task response status: {response.status_code}")
        logger.info(f"Create task response body: {response.text[:500]}")
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            error_msg = error_data.get('message', response.text)
            raise RuntimeError(f"Wan 2.6 Image task creation failed: {error_msg}")
        
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
        
        max_wait_time = 600  # 10 minutes max wait (I2I can take longer)
        poll_interval = 10   # Poll every 10 seconds
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
                # Extract image URL from choices
                choices = poll_result.get('output', {}).get('choices', [])
                if not choices:
                    raise RuntimeError(f"No choices in completed task: {poll_result}")
                
                first_choice = choices[0]
                content = first_choice.get('message', {}).get('content', [])
                if not content:
                    raise RuntimeError(f"No content in choice: {first_choice}")
                
                image_url = content[0].get('image')
                if not image_url:
                    raise RuntimeError(f"No image URL in content: {content}")
                
                logger.info(f"Task completed. Image URL: {image_url}")
                return image_url
            
            elif task_status == 'FAILED':
                # Log full response for debugging
                logger.error(f"Task {task_id} failed. Full response: {poll_result}")
                
                # Try to extract error message from various possible fields
                error_msg = (
                    poll_result.get('output', {}).get('message', '') or
                    poll_result.get('output', {}).get('code', '') or
                    poll_result.get('message', '') or
                    poll_result.get('code', '') or
                    'Unknown error - check logs for full response'
                )
                
                raise RuntimeError(f"Wan 2.6 Image task failed: {error_msg}")

            
            elif task_status in ['CANCELED', 'UNKNOWN']:
                raise RuntimeError(f"Wan 2.6 Image task {task_status}: {poll_result}")
            
            # PENDING or RUNNING - continue polling
        
        raise RuntimeError(f"Wan 2.6 Image task timed out after {max_wait_time}s")

    def _resolve_wan26_reference_image(self, path: str, model_name: str = "wan2.6-image") -> str:
        uploader = OSSImageUploader()
        backend = self._resolve_provider_backend_for_model(model_name)

        try:
            resolved = resolve_media_input(
                path,
                model_name=model_name,
                modality="image",
                backend=backend,
                uploader=uploader,
            )
            return resolved.value
        except ValueError as e:
            ref_type = classify_media_ref(path)
            if ref_type == MEDIA_REF_UNKNOWN and os.path.isabs(path) and os.path.exists(path):
                # Compatibility fallback: only for legacy absolute local paths
                # outside managed `output/` media refs.
                if uploader.is_configured:
                    object_key = uploader.upload_file(path, sub_path="temp/ref_images")
                    if object_key:
                        signed_url = uploader.sign_url_for_api(object_key)
                        if signed_url:
                            return signed_url

                return self._encode_local_image_as_data_uri(path)

            logger.warning(f"Reference image could not be resolved: {path}, reason: {e}")
            return None

    def _resolve_provider_backend_for_model(self, model_name: str) -> str:
        try:
            return resolve_provider_backend(model_name)
        except (KeyError, ValueError):
            # Keep image flows resilient for models not yet registered.
            return "dashscope"
        except Exception as e:
            logger.warning(
                f"Unexpected error resolving provider backend for model {model_name}: {e}. "
                "Falling back to dashscope."
            )
            return "dashscope"

    def _encode_local_image_as_data_uri(self, path: str) -> str:
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = "image/png"

        with open(path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")

        return f"data:{mime_type};base64,{encoded}"

    def _generate_sdk(self, prompt: str, model_name: str, size: str, n: int, negative_prompt: str, all_ref_paths: list, kwargs: dict) -> str:
        """Generate image using Dashscope SDK (for older models)."""
        call_args = {
            "model": model_name,
            "prompt": prompt,
            "n": n,
            "size": size,
        }
        
        # Add negative_prompt if provided
        if negative_prompt:
            call_args["negative_prompt"] = negative_prompt
        
        # Add remaining kwargs
        call_args.update(kwargs)
        
        logger.info(f"SDK call_args: {dict((k, v) for k, v in call_args.items() if k != 'images')}")
        # Model selection priority: explicit model_name > config params > defaults

        # Handle Reference Images for I2I
        if all_ref_paths:
            ref_image_urls = []
            uploader = OSSImageUploader()
            for path in all_ref_paths:
                if os.path.exists(path):
                    # Upload to OSS and get signed URL
                    if uploader.is_configured:
                        object_key = uploader.upload_file(path, sub_path="temp/ref_images")
                        if object_key:
                            signed_url = uploader.sign_url_for_api(object_key)
                            ref_image_urls.append(signed_url)
                            logger.info(f"Reference image uploaded, signed URL: {signed_url[:80]}...")
                        else:
                            raise RuntimeError(f"Failed to upload reference image to OSS: {path}")
                    else:
                        logger.warning(f"OSS not configured, cannot upload reference image: {path}")
                elif path.startswith("http"):
                    # Already a URL
                    ref_image_urls.append(path)
                else:
                    # Check if it's an OSS Object Key using the utility function
                    from ..utils.oss_utils import is_object_key
                    if is_object_key(path):
                        if uploader.is_configured:
                            signed_url = uploader.sign_url_for_api(path)
                            ref_image_urls.append(signed_url)
                            logger.info(f"Reference image (Object Key), signed URL: {signed_url[:80]}...")
                        else:
                            raise ValueError(f"OSS not configured but Object Key provided: {path}")
                    else:
                        raise ValueError(f"Reference image not found: {path}")
            
            logger.info(f"DEBUG: ref_image_urls count: {len(ref_image_urls)}")
            
            # Limit is already handled in generate(), but we keep a safety slice here
            ref_limit = 4 if model_name == 'wan2.6-image' else 3
            if len(ref_image_urls) > ref_limit:
                logger.warning(f"Limiting reference images from {len(ref_image_urls)} to {ref_limit}")
                ref_image_urls = ref_image_urls[:ref_limit]
            
            call_args['images'] = ref_image_urls

        # Call Dashscope SDK
        rsp = ImageSynthesis.call(**call_args)
        
        logger.info(f"SDK response: {rsp}")

        if rsp.status_code != HTTPStatus.OK:
            logger.error(f"Task failed with status code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}")
            raise RuntimeError(f"Task failed: {rsp.message}")

        # Extract Image URL
        if hasattr(rsp, 'output'):
            logger.info(f"Response Output: {rsp.output}")
            results = rsp.output.get('results')
            url = rsp.output.get('url')
            
            if results and len(results) > 0:
                 first_result = results[0]
                 if isinstance(first_result, dict):
                     image_url = first_result.get('url')
                 else:
                     image_url = getattr(first_result, 'url', None)
            elif url:
                 image_url = url
            else:
                 logger.error(f"Unexpected response structure. Output: {rsp.output}")
                 raise RuntimeError("Could not find image URL in response.")
        else:
             logger.error(f"Response has no output. Response: {rsp}")
             raise RuntimeError("Response has no output.")
        
        return image_url

    def _download_image(self, url: str, output_path: str):
        logger.info(f"Downloading image to {output_path}...")
        
        # Setup retry strategy
        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        http = requests.Session()
        http.mount("https://", adapter)
        http.mount("http://", adapter)

        temp_path = output_path + ".tmp"
        try:
            response = http.get(url, stream=True, timeout=60, verify=False) # verify=False to avoid some SSL issues
            response.raise_for_status()
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Atomic rename
            os.rename(temp_path, output_path)
            logger.info("Download complete.")
            
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise


class OpenAIImageModel(ImageGenModel):
    """Native OpenAI Image API adapter for GPT Image generation and editing.

    This adapter mirrors the existing image-generation contract used by
    ``WanxImageModel`` and ``MuleRouterImageModel``: callers pass a prompt and a
    single output path, and the adapter writes the first generated image to that
    path. Reference images switch the request from generation to edit mode.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.params = config.get("params", {})
        self._client = None

    @property
    def api_key(self) -> Optional[str]:
        api_key = (
            self.config.get("api_key")
            or self.params.get("api_key")
            or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            logger.warning("OPENAI_API_KEY not found in config or environment variables.")
        return api_key

    @property
    def base_url(self) -> str:
        return (
            self.config.get("base_url")
            or self.params.get("base_url")
            or os.getenv("OPENAI_API_BASE")
            or "https://api.openai.com/v1"
        )

    def generate(
        self,
        prompt: str,
        output_path: str,
        ref_image_path: str = None,
        ref_image_paths: list = None,
        model_name: str = None,
        **kwargs,
    ) -> Tuple[str, float]:
        all_ref_paths = self._collect_reference_paths(ref_image_path, ref_image_paths)

        final_model_name = (
            model_name
            or kwargs.pop("model", None)
            or self.params.get("model_name")
        )
        size = _normalize_openai_image_size(
            kwargs.pop("size", self.params.get("size")),
            final_model_name,
        )
        n = int(kwargs.pop("n", self.params.get("n", 1)))
        quality = kwargs.pop(
            "quality",
            self.params.get("quality"),
        )
        background = kwargs.pop("background", self.params.get("background"))
        output_format = _normalize_openai_output_format(
            kwargs.pop("output_format", self.params.get("output_format")),
            output_path,
        )
        output_compression = kwargs.pop(
            "output_compression",
            self.params.get("output_compression"),
        )
        moderation = kwargs.pop("moderation", self.params.get("moderation"))
        input_fidelity = kwargs.pop(
            "input_fidelity",
            self.params.get("input_fidelity"),
        )
        mask_path = kwargs.pop("mask_path", kwargs.pop("mask", None))

        if background == "transparent" and output_format not in {"png", "webp"}:
            raise ValueError("OpenAI transparent background requires png or webp output format.")
        if final_model_name == "gpt-image-2" and background == "transparent":
            raise ValueError(
                "gpt-image-2 does not support transparent backgrounds. "
                "Use gpt-image-1.5 with png/webp output if transparency is required."
            )
        if final_model_name == "gpt-image-2" and input_fidelity is not None:
            raise ValueError("input_fidelity is not supported by gpt-image-2.")

        payload = {
            "model": final_model_name,
            "prompt": prompt,
            "n": n,
            "size": size,
            "quality": quality,
            "output_format": output_format,
        }
        if background is not None:
            payload["background"] = background
        if output_compression is not None:
            payload["output_compression"] = output_compression
        if moderation is not None:
            payload["moderation"] = moderation
        if input_fidelity is not None:
            payload["input_fidelity"] = input_fidelity

        logger.info(
            f"Calling OpenAI Image API ({'edit' if all_ref_paths else 'generation'}): "
            f"model={final_model_name}, size={size}, n={n}"
        )

        client = self._get_client()
        api_start_time = time.time()

        if all_ref_paths:
            result = self._edit(client, payload, all_ref_paths, mask_path)
        else:
            result = client.images.generate(**payload)

        api_duration = time.time() - api_start_time
        self._write_first_image(result, output_path)
        logger.info(f"OpenAI Image API done in {api_duration:.2f}s -> {output_path}")
        return output_path, api_duration

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai>=1.0.0"
                ) from exc

            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY not set in environment")

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    def _edit(
        self,
        client,
        payload: Dict[str, Any],
        ref_image_paths: List[str],
        mask_path: Optional[str],
    ):
        with ExitStack() as stack:
            image_files = [
                self._open_reference_image(ref, stack)
                for ref in ref_image_paths
            ]
            request = dict(payload)
            request["image"] = image_files if len(image_files) > 1 else image_files[0]
            if mask_path:
                request["mask"] = self._open_reference_image(mask_path, stack)
            return client.images.edit(**request)

    def _collect_reference_paths(
        self,
        ref_image_path: Optional[str],
        ref_image_paths: Optional[List[str]],
    ) -> List[str]:
        refs: List[str] = []
        if ref_image_path:
            refs.append(ref_image_path)
        if ref_image_paths:
            refs.extend(ref_image_paths)

        deduped: List[str] = []
        seen = set()
        for ref in refs:
            if not ref or ref in seen:
                continue
            deduped.append(ref)
            seen.add(ref)
        return deduped

    def _open_reference_image(self, ref: str, stack: ExitStack):
        if ref.startswith("data:"):
            temp_path = self._write_data_uri_to_temp(ref, stack)
            return stack.enter_context(open(temp_path, "rb"))

        if ref.startswith(("http://", "https://")):
            temp_path = self._download_reference_to_temp(ref, stack)
            return stack.enter_context(open(temp_path, "rb"))

        local_path = self._resolve_local_reference(ref)
        if local_path:
            return stack.enter_context(open(local_path, "rb"))

        if is_object_key(ref):
            uploader = OSSImageUploader()
            if uploader.is_configured:
                signed_url = uploader.sign_url_for_api(ref)
                if signed_url:
                    temp_path = self._download_reference_to_temp(signed_url, stack)
                    return stack.enter_context(open(temp_path, "rb"))

        raise ValueError(
            "OpenAI image edit requires each reference image to be a local path, "
            "data URI, HTTP(S) URL, or OSS object key with OSS configured."
        )

    def _resolve_local_reference(self, ref: str) -> Optional[str]:
        candidates = [Path(ref)]
        if not Path(ref).is_absolute():
            candidates.append(Path("output") / ref)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def _write_data_uri_to_temp(self, data_uri: str, stack: ExitStack) -> str:
        if ";base64," not in data_uri:
            raise ValueError("Only base64 data URI reference images are supported.")
        header, encoded = data_uri.split(";base64,", 1)
        mime_type = header.removeprefix("data:") or "image/png"
        suffix = _media_suffix_from_mime(mime_type)
        data = base64.b64decode(encoded)
        return self._write_temp_bytes(data, suffix, stack)

    def _download_reference_to_temp(self, url: str, stack: ExitStack) -> str:
        response = requests.get(url, stream=True, timeout=120)
        response.raise_for_status()
        suffix = (
            Path(urlparse(url).path).suffix
            or _media_suffix_from_mime(response.headers.get("Content-Type"))
        )
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp.name
        try:
            with temp:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        temp.write(chunk)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
        stack.callback(lambda path: os.path.exists(path) and os.remove(path), temp_path)
        return temp_path

    def _write_temp_bytes(self, data: bytes, suffix: str, stack: ExitStack) -> str:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp.name
        try:
            with temp:
                temp.write(data)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
        stack.callback(lambda path: os.path.exists(path) and os.remove(path), temp_path)
        return temp_path

    def _write_first_image(self, result, output_path: str) -> None:
        data = getattr(result, "data", None)
        if data is None and isinstance(result, dict):
            data = result.get("data")
        if not data:
            raise RuntimeError(f"OpenAI Image API returned no image data: {result}")

        first = data[0]
        image_b64 = getattr(first, "b64_json", None)
        if image_b64 is None and isinstance(first, dict):
            image_b64 = first.get("b64_json")

        if image_b64:
            self._write_image_bytes(base64.b64decode(image_b64), output_path)
            return

        image_url = getattr(first, "url", None)
        if image_url is None and isinstance(first, dict):
            image_url = first.get("url")
        if image_url:
            self._download_image_url(image_url, output_path)
            return

        raise RuntimeError(f"OpenAI Image API response did not include b64_json or url: {first}")

    def _write_image_bytes(self, image_bytes: bytes, output_path: str) -> None:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        temp_path = output_path + ".tmp"
        try:
            with open(temp_path, "wb") as f:
                f.write(image_bytes)
            os.replace(temp_path, output_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _download_image_url(self, url: str, output_path: str) -> None:
        response = requests.get(url, stream=True, timeout=120)
        response.raise_for_status()
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        temp_path = output_path + ".tmp"
        try:
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            os.replace(temp_path, output_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
