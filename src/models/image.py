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
        self.api_key = config.get('api_key')
        if not self.api_key:
            self.api_key = os.getenv("DASHSCOPE_API_KEY")
            
        if not self.api_key:
             logger.warning("Dashscope API Key not found in config or environment variables.")
        
        dashscope.api_key = self.api_key
        self.params = config.get('params', {})

    def generate(self, prompt: str, output_path: str, ref_image_path: str = None, ref_image_paths: list = None, **kwargs) -> Tuple[str, float]:
        # Determine model based on whether reference image is provided
        # Support both single path (legacy) and list of paths
        all_ref_paths = []
        if ref_image_path:
            all_ref_paths.append(ref_image_path)
        if ref_image_paths:
            all_ref_paths.extend(ref_image_paths)
            
        # Remove duplicates
        all_ref_paths = list(set(all_ref_paths))

        if all_ref_paths:
            model_name = self.params.get('i2i_model_name', 'wan2.5-i2i-preview')
            logger.info(f"Using I2I model: {model_name} with {len(all_ref_paths)} reference images")
        else:
            model_name = self.params.get('model_name', 'wan2.2-t2i-plus')
            logger.info(f"Using T2I model: {model_name}")

        size = self.params.get('size', '1024*1024')
        n = self.params.get('n', 1)
        
        logger.info(f"Starting image generation...")
        logger.info(f"Prompt: {prompt}")
        
        try:
            api_start_time = time.time()
            
            call_args = {
                "model": model_name,
                "prompt": prompt,
                "n": n,
                "size": size,
                **kwargs
            }

            # Handle Reference Images for I2I
            if all_ref_paths:
                ref_image_urls = []
                for path in all_ref_paths:
                    if not os.path.exists(path):
                        raise ValueError(f"Reference image not found: {path}")
                    
                    # Upload to OSS
                    url = OSSImageUploader().upload_image(path)
                    if not url:
                        raise RuntimeError(f"Failed to upload reference image to OSS: {path}")
                    ref_image_urls.append(url)
                    print(f"Reference image uploaded: {url}")
                
                print(f"DEBUG: ref_image_urls count: {len(ref_image_urls)}")
                print(f"DEBUG: ref_image_urls: {ref_image_urls}")
                
                # Limit to 3 images to avoid "InvalidParameter" error (suspected limit)
                if len(ref_image_urls) > 3:
                    print(f"WARNING: Limiting reference images from {len(ref_image_urls)} to 3")
                    ref_image_urls = ref_image_urls[:3]
                
                call_args['images'] = ref_image_urls

            # Call Dashscope API
            rsp = ImageSynthesis.call(**call_args)
            
            api_end_time = time.time()
            api_duration = api_end_time - api_start_time
            
            logger.info(f"Final response: {rsp}")

            if rsp.status_code != HTTPStatus.OK:
                logger.error(f"Task failed with status code: {rsp.status_code}, code: {rsp.code}, message: {rsp.message}")
                raise RuntimeError(f"Task failed: {rsp.message}")

            # Extract Image URL
            if hasattr(rsp, 'output'):
                logger.info(f"Response Output: {rsp.output}")
                # DashScope objects behave like dicts, use .get() to avoid KeyError in __getattr__
                results = rsp.output.get('results')
                url = rsp.output.get('url')
                
                if results and len(results) > 0:
                     # results[0] might be a dict or object
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

            logger.info(f"Generation success. Image URL: {image_url}")
            
            # Download image
            self._download_image(image_url, output_path)
            return output_path, api_duration

        except Exception as e:
            import traceback
            logger.error(f"Error during generation: {e}")
            logger.error(traceback.format_exc())
            raise

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
            response = http.get(url, stream=True, timeout=30, verify=False) # verify=False to avoid some SSL issues
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
