import os
import time
from typing import Dict, Any, List
from .models import StoryboardFrame, Character, Scene, Prop, GenerationStatus, ImageAsset, ImageVariant
from ...models.image import WanxImageModel
from ...utils import get_logger

logger = get_logger(__name__)

class StoryboardGenerator:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.model = WanxImageModel(self.config.get('model', {}))
        self.output_dir = self.config.get('output_dir', 'output/storyboard')

    def generate_storyboard(self, script: Any) -> Any:
        """Generates images for all frames in the storyboard."""
        logger.info(f"Generating storyboard for script: {script.title}")
        
        total_frames = len(script.frames)
        for i, frame in enumerate(script.frames):
            logger.info(f"Generating frame {i+1}/{total_frames}: {frame.id}")
            
            # Skip if already completed (unless force regeneration is needed, but for now we skip)
            if frame.status == GenerationStatus.COMPLETED and frame.image_url:
                continue
                
            # Find scene for this frame
            scene = next((s for s in script.scenes if s.id == frame.scene_id), None)
            
            self.generate_frame(frame, script.characters, scene)
            
        return script

    def generate_frame(self, frame: StoryboardFrame, characters: List[Character], scene: Scene, ref_image_path: str = None, ref_image_paths: List[str] = None, prompt: str = None, batch_size: int = 1, size: str = None) -> StoryboardFrame:
        """Generates a storyboard frame image."""
        frame.status = GenerationStatus.PROCESSING
        
        # Default size for storyboard (landscape)
        effective_size = size or "1024*576"
        
        # Construct a rich prompt using character and scene details
        char_descriptions = []
        
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
                    target_url = None
                    source = "none"
                    
                    # Priority 1: Use selected variant from three_view_asset
                    if char.three_view_asset and char.three_view_asset.selected_id:
                        selected_variant = next((v for v in char.three_view_asset.variants if v.id == char.three_view_asset.selected_id), None)
                        if selected_variant:
                            target_url = selected_variant.url
                            source = f"three_view_asset"
                    
                    # Priority 2: Use selected variant from full_body_asset
                    if not target_url and char.full_body_asset and char.full_body_asset.selected_id:
                        selected_variant = next((v for v in char.full_body_asset.variants if v.id == char.full_body_asset.selected_id), None)
                        if selected_variant:
                            target_url = selected_variant.url
                            source = f"full_body_asset"
                    
                    # Priority 3: Use selected variant from headshot_asset
                    if not target_url and char.headshot_asset and char.headshot_asset.selected_id:
                        selected_variant = next((v for v in char.headshot_asset.variants if v.id == char.headshot_asset.selected_id), None)
                        if selected_variant:
                            target_url = selected_variant.url
                            source = f"headshot_asset"
                    
                    # Priority 4: Fallback to legacy fields
                    if not target_url:
                        target_url = char.three_view_image_url or char.full_body_image_url or char.headshot_image_url or char.avatar_url or char.image_url
                        source = "legacy_fields"
                    
                    logger.info(f"[Storyboard] Character '{char.name}' reference: source={source}, url={target_url}")
                    
                    if target_url:
                        potential_path = os.path.join("output", target_url)
                        if os.path.exists(potential_path):
                            asset_ref_paths.append(os.path.abspath(potential_path))
                        elif os.path.exists(target_url):
                            asset_ref_paths.append(os.path.abspath(target_url))
            
            # Add scene reference image
            scene_url = None
            if scene:
                if scene.image_asset and scene.image_asset.selected_id:
                    selected_variant = next((v for v in scene.image_asset.variants if v.id == scene.image_asset.selected_id), None)
                    if selected_variant:
                        scene_url = selected_variant.url
                if not scene_url:
                    scene_url = scene.image_url
                
                if scene_url:
                    potential_path = os.path.join("output", scene_url)
                    if os.path.exists(potential_path):
                        asset_ref_paths.append(os.path.abspath(potential_path))
                    elif os.path.exists(scene_url):
                        asset_ref_paths.append(os.path.abspath(scene_url))
        
        # Collect character descriptions for prompt building
        for char_id in frame.character_ids:
            char = next((c for c in characters if c.id == char_id), None)
            if char:
                char_descriptions.append(f"{char.name} ({char.description})")
        
        char_text = ", ".join(char_descriptions)

        # Remove duplicates
        asset_ref_paths = list(set(asset_ref_paths))
        
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
                
                # Use I2I if reference images are available
                # Pass collected asset paths to model
                self.model.generate(prompt, output_path, ref_image_paths=asset_ref_paths, size=effective_size)
                
                # Store relative path for frontend serving
                rel_path = os.path.relpath(output_path, "output")
                
                # Create Variant
                variant = ImageVariant(
                    id=variant_id,
                    url=rel_path,
                    prompt=prompt,
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
        except Exception as e:
            logger.error(f"Failed to generate frame {frame.id}: {e}")
            frame.status = GenerationStatus.FAILED
            
        return frame
