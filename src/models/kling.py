"""Kling video generation model adapter.

API: https://api-beijing.klingai.com (可灵 API 2.0, 路径式模型端点)
Auth: Bearer <API Key> (开放平台单 key, 不再用 AK/SK JWT)
Models: kling-3.0 / kling-3.0-turbo (模型 ID 位于路径中, 新版设计标准)

API 2.0 迁移要点 (2026-08 实测):
- 旧版: POST /v1/videos/image2video, model_name 在 body, AK/SK 签 JWT
- 新版: POST /image-to-video/{model_id}, 模型在路径, Bearer 单 key
- 新版请求体: contents[] / settings{} / options{} 三层结构
- 新版轮询: GET /tasks?task_ids=xxx (批量), 返回 data[] 数组
"""

import logging
import os
import time
from typing import Dict, Any, Tuple

import requests

from .base import VideoGenModel
from ..utils.endpoints import get_provider_base_url

logger = logging.getLogger(__name__)


class KlingModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # API 2.0: 开放平台单 key, Bearer 直用
        self.api_key = config.get("api_key") or os.getenv("KLING_API_KEY", "")
        # 模型 ID (路径式, 新版标准): kling-3.0 / kling-3.0-turbo / kling-3.0-omni
        self.model_name = config.get("params", {}).get("model_name", "kling-3.0")

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _resolve_image_input(self, img_url: str = None, img_path: str = None) -> str:
        """解析图片输入为 URL (新版 first_frame 需要 url)。"""
        if img_url and img_url.startswith(("http://", "https://")):
            return img_url
        if img_path and os.path.exists(img_path):
            # 本地文件需上传到可灵 CDN 或用 data URL。
            # 简化: 上传逻辑可参考 utils/oss_utils, 这里先支持远程 URL。
            raise ValueError(
                "API 2.0 需要图片 URL; 本地文件请先上传 (可灵 CDN 或对象存储)"
            )
        return img_url or ""

    def generate(self, prompt: str, output_path: str, img_url: str = None,
                 img_path: str = None, **kwargs) -> Tuple[str, float]:
        """Generate video using Kling API 2.0 (T2V or I2V)."""
        if not self.api_key:
            raise RuntimeError("KLING_API_KEY 未配置")

        duration = int(kwargs.get("duration", 5))
        # 参数翻译: 旧参数 std/pro → 新版 resolution 值
        _res_map = {"std": "720p", "pro": "1080p", "standard": "720p"}
        mode_in = kwargs.get("mode", "pro")
        resolution = kwargs.get("resolution") or _res_map.get(mode_in, mode_in)
        audio = kwargs.get("sound", "off")      # "on" or "off"
        negative_prompt = kwargs.get("negative_prompt", "")
        aspect_ratio = kwargs.get("aspect_ratio", "16:9")
        cfg_scale = kwargs.get("cfg_scale")

        start_time = time.time()
        is_i2v = bool(img_url or img_path)
        base_url = get_provider_base_url("KLING")

        # 组装 contents (新版)
        contents: list = [{"type": "prompt", "text": prompt}]
        if negative_prompt:
            contents.append({"type": "negative_prompt", "text": negative_prompt})
        if is_i2v:
            image_url = self._resolve_image_input(img_url, img_path)
            contents.append({"type": "first_frame", "url": image_url})

        # 组装 settings (新版)
        settings: Dict[str, Any] = {
            "resolution": resolution,
            "duration": duration,
            "audio": audio,
            "multi_shot": False,
        }
        if cfg_scale is not None:
            settings["cfg_scale"] = cfg_scale
        if aspect_ratio:
            settings["aspect_ratio"] = aspect_ratio

        # options (新版, 可选)
        options: Dict[str, Any] = {}
        if kwargs.get("callback_url"):
            options["callback_url"] = kwargs["callback_url"]

        body = {
            "contents": contents,
            "settings": settings,
            "options": options,
        }

        # 提交任务: POST /image-to-video/{model_id}
        submit_url = f"{base_url}/image-to-video/{self.model_name}"
        logger.info(f"[Kling] Submitting {'i2v' if is_i2v else 't2v'} task (model={self.model_name})")
        response = requests.post(submit_url, headers=self._auth_headers(), json=body, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(
                f"Kling submit HTTP {response.status_code}: {response.text[:300]}"
            )
        task_data = response.json()

        if task_data.get("code") != 0:
            raise RuntimeError(
                f"Kling API error (code {task_data.get('code')}): "
                f"{task_data.get('message', 'unknown error')}"
            )

        task_id = task_data["data"]["id"]
        logger.info(f"[Kling] Task submitted: {task_id}")

        # 轮询: GET /tasks?task_ids=xxx
        max_wait = 600
        poll_interval = 10
        elapsed = 0
        poll_url = f"{base_url}/tasks?task_ids={task_id}"

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            resp = requests.get(poll_url, headers=self._auth_headers(), timeout=30)
            resp.raise_for_status()
            result_data = resp.json()

            if result_data.get("code") != 0:
                raise RuntimeError(f"Kling poll error: {result_data.get('message')}")

            tasks = result_data.get("data", [])
            if not tasks:
                logger.warning(f"[Kling] No task data yet ({elapsed}s)")
                continue

            status = tasks[0].get("status")
            logger.info(f"[Kling] Task status: {status} ({elapsed}s)")

            if status == "succeeded":
                outputs = tasks[0].get("outputs", [])
                video_url = None
                for out in outputs:
                    if out.get("type") == "video" and out.get("url"):
                        video_url = out["url"]
                        break
                if not video_url and outputs:
                    video_url = outputs[0].get("url")

                if not video_url:
                    raise RuntimeError("Kling task succeeded but no video URL found")

                # 下载视频
                video_content = requests.get(video_url, timeout=120).content
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(video_content)

                generation_time = time.time() - start_time
                logger.info(f"[Kling] Done in {generation_time:.1f}s -> {output_path}")
                return output_path, generation_time

            elif status == "failed":
                msg = tasks[0].get("message", "Unknown error")
                raise RuntimeError(f"Kling task failed: {msg}")

        raise RuntimeError(f"Kling task timed out after {max_wait}s")
