"""Mock provider — 无 API key 期的全链路回归与联调用。

设计（T-B2 步骤 4）:
- MockModel（视频, VideoGenModel）与 MockImageModel（图像, ImageGenModel）
  秒回本地假文件，不打任何网络请求，不读 .env，不触碰真实 provider；
- 支持 fail_sequence 注入失败序列（如 ["boom"] 第一次失败第二次成功），
  用于测试上游的重试/错误处理路径；
- factory.py 与 pipeline / assets 的分发按模型名前缀 "mock-" 路由到这里。

假文件是带魔数的占位字节（不是可播放的 mp4/png），只服务于链路回归、
任务状态流转与下载/落盘路径的验证；真实媒体质量验证仍需真 provider。
"""

import os
import time
import logging
from typing import Dict, Any, Tuple, List

from .base import VideoGenModel
from .image import ImageGenModel

logger = logging.getLogger(__name__)

VIDEO_MAGIC = b"MOCKVIDEO\x00"
IMAGE_MAGIC = b"MOCKIMAGE\x00"


class MockError(RuntimeError):
    """mock 注入失败（fail_sequence 弹出的错误消息）。"""


class _MockGeneratorBase:
    """共享：fail_sequence 注入 + 假文件落盘。"""

    def _init_config(self, config: Dict[str, Any]):
        self.fail_sequence: List[str] = list(config.get("fail_sequence", []))
        self.latency: float = float(config.get("latency", 0.0))

    def _next_injected_failure(self) -> str:
        if self.fail_sequence:
            return self.fail_sequence.pop(0)
        return ""

    def _write_mock_file(self, output_path: str, magic: bytes, prompt: str) -> None:
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(magic + prompt.encode("utf-8")[:256])


class MockModel(_MockGeneratorBase, VideoGenModel):
    """假视频生成器：秒回本地假 mp4 占位文件。"""

    def __init__(self, config: Dict[str, Any]):
        VideoGenModel.__init__(self, config)
        self._init_config(config or {})

    def generate(self, prompt: str, output_path: str, img_url: str = None,
                 img_path: str = None, **kwargs) -> Tuple[str, float]:
        start = time.time()
        failure = self._next_injected_failure()
        if failure:
            logger.warning("[Mock] video generation injected failure: %s", failure)
            raise MockError(f"mock provider 注入失败: {failure}")
        if self.latency > 0:
            time.sleep(self.latency)
        self._write_mock_file(output_path, VIDEO_MAGIC, prompt)
        elapsed = time.time() - start
        logger.info("[Mock] video generated in %.2fs -> %s", elapsed, output_path)
        return output_path, elapsed


class MockImageModel(_MockGeneratorBase, ImageGenModel):
    """假图像生成器：秒回本地假图片占位文件。"""

    def __init__(self, config: Dict[str, Any]):
        ImageGenModel.__init__(self, config)
        self._init_config(config or {})

    def generate(self, prompt: str, output_path: str, ref_image_path: str = None,
                 ref_image_paths: list = None, model_name: str = None, **kwargs) -> Tuple[str, float]:
        start = time.time()
        failure = self._next_injected_failure()
        if failure:
            logger.warning("[Mock] image generation injected failure: %s", failure)
            raise MockError(f"mock provider 注入失败: {failure}")
        if self.latency > 0:
            time.sleep(self.latency)
        self._write_mock_file(output_path, IMAGE_MAGIC, prompt)
        elapsed = time.time() - start
        logger.info("[Mock] image generated in %.2fs -> %s", elapsed, output_path)
        return output_path, elapsed
