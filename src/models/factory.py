from .wanx import WanxModel
from ..utils import get_logger

logger = get_logger(__name__)

class ModelFactory:
    @staticmethod
    def create_model(config, *, env=None):
        model_name = config.get('model.name')
        runtime_env = env if env is not None else config.get('runtime.env')
        if model_name == 'wanx':
            return WanxModel(config.get('model'), env=runtime_env)
        elif model_name in ('kling', 'kling-v3'):
            from .kling import KlingModel
            return KlingModel(config.get('model') or {}, env=runtime_env)
        elif model_name in ('vidu', 'viduq3-pro', 'viduq3-turbo'):
            from .vidu import ViduModel
            return ViduModel(config.get('model') or {}, env=runtime_env)
        elif model_name in ('seedance', 'seedance-2.0'):
            from .mulerouter import MuleRouterVideoModel
            return MuleRouterVideoModel(config.get('model') or {}, env=runtime_env)
        else:
            raise ValueError(f"Unknown model: {model_name}")
