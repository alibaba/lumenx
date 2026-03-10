from .wanx import WanxModel
from ..utils import get_logger

logger = get_logger(__name__)

class ModelFactory:
    @staticmethod
    def create_model(config):
        model_name = config.get('model.name')
        if model_name == 'wanx':
            return WanxModel(config.get('model'))
        elif model_name in ('kling', 'kling-v3'):
            from .kling import KlingModel
            return KlingModel(config.get('model') or {})
        elif model_name in ('vidu', 'viduq3-pro', 'viduq3-turbo'):
            from .vidu import ViduModel
            return ViduModel(config.get('model') or {})
        else:
            raise ValueError(f"Unknown model: {model_name}")
