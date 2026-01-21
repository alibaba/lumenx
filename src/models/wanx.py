import os
import time
import requests
from http import HTTPStatus
from dashscope import VideoSynthesis
import dashscope
from .base import VideoGenModel
from ..utils import get_logger

from typing import Tuple

from ..utils.oss_utils import OSSImageUploader

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

    def generate(self, prompt: str, output_path: str, img_path: str = None, model_name: str = None, **kwargs) ->Tuple[str, float]:
        # Determine model - allow explicit override via model_name param or 'model' kwarg
        # Fix: pipeline.py passes 'model=task.model', we need to accept both
        if model_name:
            final_model_name = model_name
        elif kwargs.get('model'):
            final_model_name = kwargs.get('model')
            logger.info(f"Using model from kwargs: {final_model_name}")
        elif img_path or kwargs.get('img_url'):
            final_model_name = self.params.get('i2v_model_name', 'wan2.6-i2v')  # Default to I2V model
            logger.info(f"Using I2V model: {final_model_name}")
        else:
            final_model_name = self.params.get('model_name', 'wan2.5-t2v-preview')
            logger.info(f"Using T2V model: {final_model_name}")

        size = self.params.get('size', '1280*720')
        prompt_extend = self.params.get('prompt_extend', True)
        watermark = self.params.get('watermark', False)

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

            # Get image URL (upload local file if needed, or convert Object Key to signed URL)
            img_url = kwargs.get('img_url')
            uploader = OSSImageUploader()
            
            if img_path:
                if os.path.exists(img_path):
                    # Local file - upload to OSS and get signed URL
                    if uploader.is_configured:
                        logger.info(f"Uploading input image to OSS: {img_path}")
                        object_key = uploader.upload_file(img_path, sub_path="temp/i2v_input")
                        if object_key:
                            img_url = uploader.sign_url_for_api(object_key)
                            logger.info(f"Input image uploaded, signed URL: {img_url[:80]}...")
                        else:
                            raise RuntimeError("Failed to upload input image to OSS")
                    else:
                        raise RuntimeError("OSS not configured, cannot upload input image for I2V")
                elif img_path.startswith("http"):
                    # Already a URL
                    img_url = img_path
                elif "/" in img_path and not img_path.startswith("output/"):
                    # Might be an Object Key, generate signed URL
                    if uploader.is_configured:
                        img_url = uploader.sign_url_for_api(img_path)
                        logger.info(f"Input image (Object Key from img_path), signed URL: {img_url[:80]}...")
                    else:
                        raise RuntimeError(f"OSS not configured, cannot sign Object Key: {img_path}")
                else:
                    raise ValueError(f"Input image not found: {img_path}")
            elif img_url:
                # If img_url is provided but img_path is not, check if img_url is an Object Key
                if not img_url.startswith("http") and "/" in img_url and not img_url.startswith("output/"):
                    if uploader.is_configured:
                        img_url = uploader.sign_url_for_api(img_url)
                        logger.info(f"Input image (Object Key from img_url), signed URL: {img_url[:80]}...")
                    else:
                        logger.warning(f"OSS not configured, cannot sign Object Key in img_url: {img_url}")

            # Use HTTP API for wan2.6-i2v, wan2.5-i2v, or wan2.6-r2v
            if final_model_name in ['wan2.6-i2v', 'wan2.5-i2v']:
                # Get shot_type from kwargs (only for wan I2V models)
                shot_type = kwargs.get('shot_type', 'single')
                video_url = self._generate_wan_i2v_http(
                    prompt=prompt,
                    img_url=img_url,
                    model_name=final_model_name,
                    resolution=resolution,
                    duration=duration,
                    prompt_extend=prompt_extend,
                    negative_prompt=negative_prompt,
                    audio_url=audio_url,
                    watermark=watermark,
                    seed=seed,
                    shot_type=shot_type
                )
            elif final_model_name == 'wan2.6-r2v':
                # R2V generation
                ref_video_urls = kwargs.get('ref_video_urls', [])
                if not ref_video_urls:
                    raise ValueError("ref_video_urls is required for wan2.6-r2v")
                
                # Process ref_video_urls: Upload local files or sign Object Keys
                processed_ref_urls = []
                for ref_url in ref_video_urls:
                    final_url = ref_url
                    
                    # Check if it's a local file
                    local_path = None
                    if not ref_url.startswith("http"):
                        # Check relative to output dir
                        potential_path = os.path.join("output", ref_url)
                        if os.path.exists(potential_path):
                            local_path = potential_path
                        # Check absolute path or relative to CWD
                        elif os.path.exists(ref_url):
                            local_path = ref_url
                    
                    if local_path:
                        # Local file - upload to OSS
                        if uploader.is_configured:
                            logger.info(f"Uploading reference video to OSS: {local_path}")
                            object_key = uploader.upload_file(local_path, sub_path="temp/r2v_input")
                            if object_key:
                                final_url = uploader.sign_url_for_api(object_key)
                                logger.info(f"Reference video uploaded, signed URL: {final_url[:80]}...")
                            else:
                                raise RuntimeError(f"Failed to upload reference video: {local_path}")
                        else:
                            raise RuntimeError("OSS not configured, cannot upload local reference video for R2V")
                    
                    elif not ref_url.startswith("http") and "/" in ref_url and not ref_url.startswith("output/"):
                        # Likely an Object Key
                        if uploader.is_configured:
                            final_url = uploader.sign_url_for_api(ref_url)
                            logger.info(f"Reference video (Object Key), signed URL: {final_url[:80]}...")
                        else:
                            logger.warning(f"OSS not configured, cannot sign Object Key: {ref_url}")
                            
                    processed_ref_urls.append(final_url)
                
                ref_video_urls = processed_ref_urls
                
                shot_type = kwargs.get('shot_type', 'multi') # Default to multi for R2V as per PRD
                
                video_url = self._generate_wan_r2v_http(
                    prompt=prompt,
                    ref_video_urls=ref_video_urls,
                    model_name=final_model_name,
                    size=size, # R2V uses size (e.g. 1280*720)
                    duration=duration,
                    audio=kwargs.get('audio', True), # Default to True for R2V
                    shot_type=shot_type,
                    seed=seed
                )
            else:
                # Use SDK for other models
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
                                  resolution: str = "720P", 
                                  duration: int = 5, prompt_extend: bool = True,
                                  negative_prompt: str = None, audio_url: str = None,
                                  watermark: bool = False, seed: int = None,
                                  shot_type: str = "single") -> str:
        """Generate video using Wan I2V (2.5 or 2.6) via HTTP API (asynchronous with polling)."""
        create_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"  # Required for async mode
        }
        
        payload = {
            "model": model_name,  # Use passed model name (wan2.5-i2v or wan2.6-i2v)
            "input": {
                "prompt": prompt,
                "img_url": img_url
            },
            "parameters": {
                "resolution": resolution,
                "duration": duration,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
                "audio": True,  # Auto-generate audio
                "shot_type": shot_type  # single or multi (only works when prompt_extend=True)
            }
        }
        
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
        poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
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
                                  size: str = "1280*720", 
                                  duration: int = 5, audio: bool = True,
                                  shot_type: str = "multi", seed: int = None) -> str:
        """Generate video using Wan R2V via HTTP API (asynchronous with polling)."""
        create_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable"
        }
        
        payload = {
            "model": model_name,
            "input": {
                "prompt": prompt,
                "reference_video_urls": ref_video_urls
            },
            "parameters": {
                "size": size,
                "duration": duration,
                "audio": audio,
                "shot_type": shot_type
            }
        }
        
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
        poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
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
