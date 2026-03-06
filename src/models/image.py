from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple
import os
import time
import requests
from http import HTTPStatus
import dashscope
from dashscope import ImageSynthesis
from ..utils import get_logger
from ..utils.oss_utils import OSSImageUploader

logger = get_logger(__name__)

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
            # For I2I, use i2i_model_name if configured, otherwise default to wan2.5-i2i-preview
            final_model_name = self.params.get('i2i_model_name', 'wan2.5-i2i-preview')
        else:
            # For T2I, use model_name if configured, otherwise default to wan2.6-t2i
            final_model_name = self.params.get('model_name', 'wan2.6-t2i')

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
        ref_limit = 4 if final_model_name == 'wan2.6-image' else 3
        if len(all_ref_paths) > ref_limit:
            logger.warning(f"Limiting reference images from {len(all_ref_paths)} to {ref_limit} for model {final_model_name}")
            all_ref_paths = all_ref_paths[:ref_limit]
        
        logger.info(f"Starting image generation...")
        logger.info(f"Prompt: {prompt}")
        logger.info(f"Model: {final_model_name}, Size: {size}, N: {n}")

        try:
            api_start_time = time.time()
            # Use HTTP API for wan2.6 models (SDK not supported yet)
            if final_model_name == 'wan2.6-t2i':
                image_url = self._generate_wan26_http(prompt, size, n, negative_prompt)
            elif final_model_name == 'wan2.6-image':
                # wan2.6-image for I2I (requires reference images)
                image_url = self._generate_wan26_image_http(prompt, size, n, negative_prompt, all_ref_paths)
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

    def _generate_wan26_http(self, prompt: str, size: str, n: int, negative_prompt: str = None) -> str:
        """Generate image using Wan 2.6 T2I via HTTP API (synchronous)."""
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        
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
        create_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"  # Required for async mode
        }
        
        # Build content array with text and reference images
        content = [{"text": prompt}]
        
        # Add reference images (upload to OSS first if local paths)
        if ref_image_paths:
            # Limit is already handled in generate(), but we keep a safety slice here
            # This method is specifically for wan2.6-image which supports 4 images
            ref_limit = 4
            for path in ref_image_paths[:ref_limit]:
                if os.path.exists(path):
                    # Upload local file to OSS and get signed URL for AI API
                    uploader = OSSImageUploader()
                    if uploader.is_configured:
                        object_key = uploader.upload_file(path, sub_path="temp/ref_images")
                        if object_key:
                            # Generate signed URL for AI API access (30 min validity)
                            signed_url = uploader.sign_url_for_api(object_key)
                            content.append({"image": signed_url})
                            logger.info(f"Reference image uploaded, signed URL: {signed_url[:80]}...")
                    else:
                        # OSS not configured, try to use local path (will likely fail for remote APIs)
                        logger.warning(f"OSS not configured, cannot upload reference image: {path}")
                elif path.startswith("http"):
                    # Already a URL (could be signed or public)
                    content.append({"image": path})
                else:
                    # Check if it's an OSS Object Key using the utility function
                    from ..utils.oss_utils import is_object_key
                    if is_object_key(path):
                        uploader = OSSImageUploader()
                        if uploader.is_configured:
                            # Generate signed URL for AI API access (30 min validity)
                            signed_url = uploader.sign_url_for_api(path)
                            content.append({"image": signed_url})
                            logger.info(f"Reference image (Object Key), signed URL: {signed_url[:80]}...")
                        else:
                            logger.warning(f"OSS not configured but Object Key provided: {path}")
                    else:
                        logger.warning(f"Reference image not found: {path}")
        
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
        poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
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
