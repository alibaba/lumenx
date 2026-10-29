from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple


class VideoGenModel(ABC):
    """Abstract base class for video generation models."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.output_root = self.config.get("output_root") or self.config.get("params", {}).get("output_root")

    @abstractmethod
    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        """
        Generates a video from a prompt.
        
        Args:
            prompt: The input text prompt.
            output_path: The path to save the generated video.
            **kwargs: Additional arguments.
            
        Returns:
            A tuple containing:
            - The path to the generated video file.
            - The duration of the API generation process in seconds (excluding download).
        """
        pass
