import os
import time
from types import MappingProxyType
from typing import Callable, Dict, Any, List, Optional
from .models import StoryboardFrame, Character, Scene, Prop, GenerationStatus, ImageAsset, ImageVariant
from ...models.image import WanxImageModel
from ...utils import get_logger
from ...utils.oss_utils import is_object_key
from ...utils.json_storage import StorageError

logger = get_logger(__name__)


def storyboard_reference_url(asset):
    """Keep the legacy image-channel priority shared with input checks."""
    if isinstance(asset, Character):
        channels = (asset.three_view_asset, asset.full_body_asset, asset.headshot_asset)
        fallback = (asset.three_view_image_url or asset.full_body_image_url
                    or asset.headshot_image_url or asset.avatar_url or asset.image_url)
    else:
        channels = (asset.image_asset,)
        fallback = asset.image_url
    for channel in channels:
        if channel and channel.selected_id:
            selected = next((v for v in channel.variants if v.id == channel.selected_id), None)
            if selected and selected.url:
                return selected.url
    return fallback


def prepare_storyboard_prompt(frame: StoryboardFrame, characters: List[Character], scene: Optional[Scene], prompt: Optional[str]) -> str:
    """One prompt policy for legacy rendering and frozen Core frame requests."""
    descriptions = []
    for char_id in frame.character_ids:
        char = next((c for c in characters if c.id == char_id), None)
        if char:
            descriptions.append(f"{char.name} ({char.description})")
    char_text = ", ".join(descriptions)
    if not prompt:
        prompt = f"Storyboard Frame: {frame.action_description}. "
        if char_text:
            prompt += f"Characters: {char_text}. "
        if scene:
            prompt += f"Location: {scene.name}, {scene.description}. "
        prompt += f"Camera: {frame.camera_angle}"
        if frame.camera_movement:
            prompt += f", {frame.camera_movement}"
        prompt += "."
    elif char_text and char_text not in prompt:
        prompt = f"{prompt} Characters: {char_text}."
    return prompt


def _default_output_root(output_dir: str, leaf: str) -> str:
    normalized = os.path.normpath(os.fspath(output_dir))
    if os.path.basename(normalized) == leaf:
        parent = os.path.dirname(normalized)
        if parent:
            return parent
    return "output"


def _resolve_output_ref(output_root: str, value: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None
    for prefix in ("/files/outputs/", "/files/output/", "/files/", "files/", "output/", "outputs/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    root = os.path.realpath(output_root)
    if os.path.isabs(raw):
        candidate = os.path.realpath(raw)
    else:
        candidate = os.path.realpath(os.path.join(root, raw.lstrip("/")))
    if candidate == root or candidate.startswith(root + os.sep):
        return candidate
    return None

class StoryboardGenerator:
    def __init__(
        self, config: Dict[str, Any] = None,
        *, before_generation: Optional[Callable[[], None]] = None, env=None,
    ):
        self.config = config or {}
        self._env = None if env is None else MappingProxyType(dict(env))
        self._before_generation = before_generation
        self.output_dir = self.config.get('output_dir', 'output/storyboard')
        self.output_root = self.config.get('output_root') or _default_output_root(self.output_dir, "storyboard")
        model_config = dict(self.config.get('model') or {})
        model_config.setdefault('output_root', self.output_root)
        self.model = WanxImageModel(model_config, **({"env": self._env} if self._env is not None else {}))

    def _check_storage(self, check_current: Optional[Callable[[], None]] = None) -> None:
        if self._before_generation is not None:
            self._before_generation()
        if check_current is not None:
            check_current()

    def _rel_output_path(self, path: str) -> str:
        return os.path.relpath(path, self.output_root)

    def _resolve_output_ref(self, value: str) -> Optional[str]:
        return _resolve_output_ref(self.output_root, value)

    def generate_storyboard(self, script: Any, characters: List[Character] = None, scenes: List[Scene] = None) -> Any:
        """Generates images for all frames in the storyboard.

        ``characters`` / ``scenes`` accept the resolver-merged lists
        (Episode → Series → Global). When omitted they fall back to the
        script's own local lists, preserving prior behavior."""
        logger.info(f"Generating storyboard for script: {script.title}")
        resolved_characters = characters if characters is not None else script.characters
        resolved_scenes = scenes if scenes is not None else script.scenes
        
        total_frames = len(script.frames)
        for i, frame in enumerate(script.frames):
            logger.info(f"Generating frame {i+1}/{total_frames}: {frame.id}")
            
            # Skip if already completed (unless force regeneration is needed, but for now we skip)
            if frame.status == GenerationStatus.COMPLETED and frame.image_url:
                continue
                
            # Find scene for this frame
            scene = next((s for s in resolved_scenes if s.id == frame.scene_id), None)
            
            self.generate_frame(frame, resolved_characters, scene)
            
        return script

    def generate_frame(self, frame: StoryboardFrame, characters: List[Character], scene: Scene, ref_image_path: str = None, ref_image_paths: List[str] = None, prompt: str = None, batch_size: int = 1, size: str = None, model_name: str = None, *, check_current: Optional[Callable[[], None]] = None) -> StoryboardFrame:
        """Generates a storyboard frame image."""
        self._check_storage(check_current)
        original_selection = frame.rendered_image_asset.selected_id if frame.rendered_image_asset else None
        original_image_url = frame.image_url
        original_rendered_url = frame.rendered_image_url
        frame.status = GenerationStatus.PROCESSING
        
        # Default size for storyboard (landscape)
        effective_size = size or "1024*576"
        
        # Collect reference image paths from assets
        asset_ref_paths = []
        
        # If frontend provides explicit reference paths, use them directly
        # Otherwise, auto-collect from characters and scene
        use_frontend_refs = (ref_image_paths and len(ref_image_paths) > 0) or ref_image_path
        
        if use_frontend_refs:
            # Use only what frontend provided (already selected by user)
            if ref_image_paths:
                asset_ref_paths.extend(ref_image_paths)
            if ref_image_path:
                asset_ref_paths.append(ref_image_path)
            logger.info(f"[Storyboard] Using {len(asset_ref_paths)} frontend-provided reference images")
        else:
            # Auto-collect from characters and scene (fallback for batch generation)
            for char_id in frame.character_ids:
                char = next((c for c in characters if c.id == char_id), None)
                if char:
                    # Add character reference image - prioritize selected variant from ImageAsset
                    target_url = storyboard_reference_url(char)
                    
                    if target_url:
                        if is_object_key(target_url, env=self._env):
                            asset_ref_paths.append(target_url)
                        else:
                            potential_path = self._resolve_output_ref(target_url)
                            if potential_path and os.path.exists(potential_path):
                                asset_ref_paths.append(os.path.abspath(potential_path))
            
            # Add scene reference image
            scene_url = None
            if scene:
                scene_url = storyboard_reference_url(scene)
                
                if scene_url:
                    if is_object_key(scene_url, env=self._env):
                        asset_ref_paths.append(scene_url)
                    else:
                        potential_path = self._resolve_output_ref(scene_url)
                        if potential_path and os.path.exists(potential_path):
                            asset_ref_paths.append(os.path.abspath(potential_path))
        
        # Remove duplicates
        asset_ref_paths = list(set(asset_ref_paths))
        prompt = prepare_storyboard_prompt(frame, characters, scene, prompt)
        
        # Store the optimized prompt
        frame.image_prompt = prompt
        
        # Initialize rendered_image_asset if not present
        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset(asset_id=frame.id, asset_type="storyboard_frame")

        try:
            import uuid
            
            for _ in range(batch_size):
                variant_id = str(uuid.uuid4())
                output_filename = f"{frame.id}_{variant_id}.png"
                output_path = os.path.join(self.output_dir, output_filename)
                
                # Ensure output directory exists
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

                
                # Use I2I if reference images are available
                # Pass collected asset paths to model
                logger.info(f"[Storyboard] Calling model.generate with {len(asset_ref_paths)} reference images using model {model_name or 'default'}")
                self._check_storage(check_current)
                self.model.generate(prompt, output_path, ref_image_paths=asset_ref_paths, size=effective_size, model_name=model_name)
                self._check_storage(check_current)
                
                # Store relative path for frontend serving
                rel_path = self._rel_output_path(output_path)
                
                # Create Variant
                variant = ImageVariant(
                    id=variant_id,
                    url=rel_path,
                    prompt_used=prompt,
                    created_at=time.time()
                )
                frame.rendered_image_asset.variants.append(variant)
                
                # Auto-select the latest one
                frame.rendered_image_asset.selected_id = variant_id
            
            # Sync legacy fields
            selected_variant = next((v for v in frame.rendered_image_asset.variants if v.id == frame.rendered_image_asset.selected_id), None)
            if selected_variant:
                frame.rendered_image_url = selected_variant.url
                frame.image_url = selected_variant.url
                
            frame.updated_at = time.time()
            frame.status = GenerationStatus.COMPLETED
            
            # Try uploading to OSS if configured - store Object Key (not full URL)
            try:
                from ...utils.oss_utils import OSSImageUploader
                uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                if uploader.is_configured:
                    # Upload the selected variant
                    if selected_variant:
                        # Construct local path from relative path
                        local_path = self._resolve_output_ref(selected_variant.url)
                        if local_path and os.path.exists(local_path):
                            # Upload and get Object Key (not full URL)
                            self._check_storage(check_current)
                            object_key = uploader.upload_file(
                                local_path, 
                                sub_path=f"storyboard"
                            )
                            self._check_storage(check_current)
                            if object_key:
                                logger.info(f"Uploaded frame {frame.id} to OSS: {object_key}")
                                # Store Object Key (will be converted to signed URL on API response)
                                selected_variant.url = object_key
                                frame.rendered_image_url = object_key
                                frame.image_url = object_key
            except StorageError:
                raise
            except Exception as e:
                self._check_storage(check_current)
                logger.error(f"Failed to upload frame {frame.id} to OSS: {e}")
                # Continue even if OSS upload fails
                
        except StorageError:
            raise
        except Exception as e:
            self._check_storage(check_current)
            logger.error(f"Failed to generate frame {frame.id}: {e}")
            # Partial candidates remain available, but a failed batch must not
            # silently change the user's selection or leave legacy URLs stale.
            frame.rendered_image_asset.selected_id = original_selection
            frame.image_url = original_image_url
            frame.rendered_image_url = original_rendered_url
            frame.status = GenerationStatus.FAILED
            
        return frame
