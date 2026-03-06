import os
import uuid
import time
from typing import Dict, Any, List
from urllib.parse import quote
from .models import Character, Scene, Prop, GenerationStatus, ImageAsset, ImageVariant, MAX_VARIANTS_PER_ASSET
from ...models.image import WanxImageModel
from ...model_request_settings import MODEL_REQUEST_SETTINGS
from ...utils import get_logger
from ...utils.oss_utils import is_object_key

logger = get_logger(__name__)

def cleanup_old_variants(image_asset: ImageAsset) -> None:
    """
    Enforce variant limit: keep at most MAX_VARIANTS_PER_ASSET non-favorited variants.
    Favorited variants are never removed.
    When over limit, remove oldest non-favorited variants first.
    """
    if not image_asset or not image_asset.variants:
        return
    
    favorited = [v for v in image_asset.variants if v.is_favorited]
    non_favorited = [v for v in image_asset.variants if not v.is_favorited]
    
    # Sort non-favorited by created_at (oldest first)
    non_favorited.sort(key=lambda v: v.created_at)
    
    # Keep only the most recent MAX_VARIANTS_PER_ASSET non-favorited
    if len(non_favorited) > MAX_VARIANTS_PER_ASSET:
        to_remove = len(non_favorited) - MAX_VARIANTS_PER_ASSET
        removed = non_favorited[:to_remove]
        non_favorited = non_favorited[to_remove:]
        for v in removed:
            logger.info(f"Auto-removed old variant: {v.id} (created_at: {v.created_at})")
    
    # Rebuild variants list: favorited first, then non-favorited (newest first)
    non_favorited.reverse()  # Newest first
    image_asset.variants = favorited + non_favorited

# Aspect ratio to image size mapping
ASPECT_RATIO_TO_SIZE = {
    "9:16": "576*1024",   # Portrait
    "16:9": "1024*576",   # Landscape
    "1:1": "1024*1024",   # Square
}

class AssetGenerator:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        # Default to Wanx for now, can be swapped based on config
        self.model = WanxImageModel(self.config.get('model', {}))
        self.output_dir = self.config.get('output_dir', 'output/assets')

    def generate_character(self, character: Character, generation_type: str = "all", prompt: str = "", positive_prompt: str = None, negative_prompt: str = "", batch_size: int = 1, model_name: str = None, i2i_model_name: str = None, size: str = None) -> Character:
        """
        Generates character assets based on generation_type.
        Types: 'full_body', 'three_view', 'headshot', 'all'
        """
        character.status = GenerationStatus.PROCESSING
        
        # Default style suffix if not provided (None means use default, "" means no style)
        style_suffix = positive_prompt if positive_prompt is not None else "cinematic lighting, movie still, 8k, highly detailed, realistic"
        
        # Default size if not provided
        effective_size = size or "576*1024"  # Default to portrait for characters
        
        try:
            # 1. Full Body (Master)
            if generation_type in ["all", "full_body"]:
                # Use provided prompt or construct default
                if not prompt:
                    # Default prompt - no style included, emphasize clean background
                    # If there's a reference image (reverse generation), emphasize consistency
                    base_prompt = f"Full body character design of {character.name}, concept art. {character.description}. Standing pose, neutral expression, no emotion, looking at viewer. Clean white background, isolated, no other objects, no scenery, simple background, high quality, masterpiece."
                else:
                    base_prompt = prompt
                
                # Save the user's prompt WITHOUT style suffix
                character.full_body_prompt = base_prompt
                
                # Generate the image with style suffix appended
                generation_prompt = f"{base_prompt}, {style_suffix}" if style_suffix and style_suffix not in base_prompt else base_prompt
                
                # Check for base character reference (for variants)
                ref_image_path = None
                if character.base_character_id:
                    base_fullbody_path = os.path.join(self.output_dir, 'characters', f"{character.base_character_id}_fullbody.png")
                    if os.path.exists(base_fullbody_path):
                        ref_image_path = base_fullbody_path

                # === REVERSE GENERATION: Check for uploaded images to use as reference ===
                # Priority: Three Views > Headshot (uploaded images)
                if not ref_image_path:
                    # Check for uploaded three_views
                    if character.three_view_asset:
                        uploaded_variant = next(
                            (v for v in character.three_view_asset.variants if getattr(v, 'is_uploaded_source', False)),
                            None
                        )
                        if uploaded_variant:
                            ref_url = uploaded_variant.url
                            if is_object_key(ref_url):
                                ref_image_path = ref_url
                                logger.debug(f"Reverse generation: Using uploaded three_views as reference: {ref_url}")
                            else:
                                local_path = os.path.join("output", ref_url)
                                if os.path.exists(local_path):
                                    ref_image_path = local_path
                                    logger.debug(f"Reverse generation: Using local three_views as reference: {local_path}")
                    
                    # Check for uploaded headshot
                    if not ref_image_path and character.headshot_asset:
                        uploaded_variant = next(
                            (v for v in character.headshot_asset.variants if getattr(v, 'is_uploaded_source', False)),
                            None
                        )
                        if uploaded_variant:
                            ref_url = uploaded_variant.url
                            if is_object_key(ref_url):
                                ref_image_path = ref_url
                                logger.debug(f"Reverse generation: Using uploaded headshot as reference: {ref_url}")
                            else:
                                local_path = os.path.join("output", ref_url)
                                if os.path.exists(local_path):
                                    ref_image_path = local_path
                                    logger.debug(f"Reverse generation: Using local headshot as reference: {local_path}")

                # Batch Generation Loop
                successful_generations = 0
                for i in range(batch_size):
                    try:
                        variant_id = str(uuid.uuid4())
                        fullbody_path = os.path.join(self.output_dir, 'characters', f"{character.id}_fullbody_{variant_id}.png")
                        os.makedirs(os.path.dirname(fullbody_path), exist_ok=True)
                        
                        # Select model: use I2I model (wan2.6-image) when reference image is provided
                        effective_model_name = model_name
                        effective_generation_prompt = generation_prompt
                        if ref_image_path:
                            # Override to I2I model when using reference image
                            effective_model_name = i2i_model_name or MODEL_REQUEST_SETTINGS.wanx_image_i2i_model_name_default
                            logger.debug(f"Reverse generation: Using I2I model {effective_model_name} with reference image")
                            
                            # Enhance prompt for reverse generation to emphasize reference consistency (only if not already present)
                            reverse_enhancement = "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, and clothing as the reference image. "
                            if reverse_enhancement.strip() not in effective_generation_prompt:
                                effective_generation_prompt = f"{reverse_enhancement}{generation_prompt}"
                                logger.debug(f"Reverse generation enhanced prompt: {effective_generation_prompt[:100]}...")
                        
                        self.model.generate(effective_generation_prompt, fullbody_path, ref_image_path=ref_image_path, negative_prompt=negative_prompt, model_name=effective_model_name, size=effective_size)
                        
                        rel_fullbody_path = os.path.relpath(fullbody_path, "output")
                        
                        # Store in ImageAsset
                        if not character.full_body_asset:
                            from .models import ImageAsset
                            character.full_body_asset = ImageAsset()
                            
                        from .models import ImageVariant
                        variant = ImageVariant(
                            id=variant_id,
                            url=rel_fullbody_path,
                            created_at=time.time(),
                            prompt_used=generation_prompt
                        )
                        character.full_body_asset.variants.insert(0, variant) # Prepend new variants
                        
                        # Cleanup old variants (keep max 10 non-favorited)
                        cleanup_old_variants(character.full_body_asset)
                        
                        # Auto-select if it's the first one or we want to update the view
                        if not character.full_body_asset.selected_id or batch_size == 1:
                            character.full_body_asset.selected_id = variant_id
                            character.full_body_image_url = rel_fullbody_path # Legacy sync
                        
                        successful_generations += 1
                        logger.debug(f"Full body variant {i+1}/{batch_size} generated successfully")
                        
                        # Add small delay between API calls to avoid rate limiting (except for last one)
                        if i < batch_size - 1:
                            time.sleep(1)
                    except Exception as e:
                        logger.error(f"Failed to generate full body variant {i+1}/{batch_size}: {e}")
                        # Continue with next variant instead of stopping entirely
                        continue

                    # Try uploading to OSS if configured - store Object Key (not full URL)
                    try:
                        from ...utils.oss_utils import OSSImageUploader
                        uploader = OSSImageUploader()
                        if uploader.is_configured:
                            object_key = uploader.upload_file(fullbody_path, sub_path="assets/characters")
                            if object_key:
                                logger.debug(f"Uploaded full body variant {i+1} to OSS: {object_key}")
                                variant.url = object_key
                                if character.full_body_asset.selected_id == variant.id:
                                    character.full_body_image_url = object_key
                    except Exception as e:
                        logger.error(f"Failed to upload full body variant {i+1} to OSS: {e}")

                logger.info(f"Full body generation complete: {successful_generations}/{batch_size} variants generated")
                character.full_body_updated_at = time.time()
                
                # Raise exception if all variants failed
                if successful_generations == 0:
                    raise RuntimeError("生成失败，请检查 API 配置或修改描述内容后重试。")
                
                # Mark downstream as inconsistent if generating only full body
                if generation_type == "full_body":
                    character.is_consistent = False
            
            # Ensure full body exists for derived assets
            # Use selected variant or legacy url
            current_full_body_url = character.full_body_image_url
            if character.full_body_asset and character.full_body_asset.selected_id:
                selected_variant = next((v for v in character.full_body_asset.variants if v.id == character.full_body_asset.selected_id), None)
                if selected_variant:
                    current_full_body_url = selected_variant.url

            # === REVERSE GENERATION: Allow using uploaded images as reference if no full body ===
            # Check for uploaded images to use as reference when no full body exists
            uploaded_reference_url = None
            if not current_full_body_url:
                # Check for any uploaded image that can be used as reference
                # Priority: The asset type being generated > other types
                if generation_type == "three_view" and character.headshot_asset:
                    # If generating three_view, check for uploaded headshot
                    uploaded_variant = next(
                        (v for v in character.headshot_asset.variants if getattr(v, 'is_uploaded_source', False)),
                        None
                    )
                    if uploaded_variant:
                        uploaded_reference_url = uploaded_variant.url
                        logger.debug(f"Reverse generation: Will use uploaded headshot as reference for three_view")
                
                elif generation_type == "headshot" and character.three_view_asset:
                    # If generating headshot, check for uploaded three_views
                    uploaded_variant = next(
                        (v for v in character.three_view_asset.variants if getattr(v, 'is_uploaded_source', False)),
                        None
                    )
                    if uploaded_variant:
                        uploaded_reference_url = uploaded_variant.url
                        logger.debug(f"Reverse generation: Will use uploaded three_views as reference for headshot")
                
                # Also check own asset type for uploaded source
                if not uploaded_reference_url:
                    own_asset = character.three_view_asset if generation_type == "three_view" else character.headshot_asset
                    if own_asset:
                        uploaded_variant = next(
                            (v for v in own_asset.variants if getattr(v, 'is_uploaded_source', False)),
                            None
                        )
                        if uploaded_variant:
                            uploaded_reference_url = uploaded_variant.url
                            logger.debug(f"Reverse generation: Will use own uploaded image as reference")

            if generation_type in ["three_view", "headshot"] and not current_full_body_url and not uploaded_reference_url:
                raise ValueError("Full body image is required to generate derived assets. Upload an image or generate a full body first.")
            
            # Handle reference image path: could be OSS Object Key or local path
            # Prioritize full body, fall back to uploaded reference
            reference_url = current_full_body_url or uploaded_reference_url
            if reference_url:
                if is_object_key(reference_url):
                    # OSS Object Key - pass directly, image.py will handle signing
                    fullbody_path = reference_url
                    logger.debug(f"Using OSS Object Key for reference: {reference_url}")
                else:
                    # Local relative path - prepend output directory
                    fullbody_path = os.path.join("output", reference_url)
                    logger.debug(f"Using local path for reference: {fullbody_path}")
            else:
                fullbody_path = None

            # 2. Three View Sheet (Derived)
            if generation_type in ["all", "three_view"]:
                if not prompt or generation_type == "all":
                    # Add reference consistency emphasis
                    base_prompt = f"Character Reference Sheet for {character.name}. {character.description}. Three-view character design: Front view, Side view, and Back view. STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, and clothing as the reference image. Full body, standing pose, neutral expression. Consistent clothing and details across all views. Simple white background, clean lines, studio lighting, high quality."
                else:
                    base_prompt = prompt
                
                # Save the user's prompt WITHOUT style suffix
                character.three_view_prompt = base_prompt
                
                # Generate with style suffix appended
                generation_prompt = f"{base_prompt}, {style_suffix}" if style_suffix and style_suffix not in base_prompt else base_prompt
                
                sheet_negative = negative_prompt + ", background, scenery, landscape, shadows, complex background, text, watermark, messy, distorted, extra limbs"

                successful_generations = 0
                for i in range(batch_size):
                    try:
                        variant_id = str(uuid.uuid4())
                        sheet_path = os.path.join(self.output_dir, 'characters', f"{character.id}_sheet_{variant_id}.png")
                        
                        self.model.generate(generation_prompt, sheet_path, ref_image_path=fullbody_path, negative_prompt=sheet_negative, ref_strength=0.8, model_name=i2i_model_name)
                        
                        rel_sheet_path = os.path.relpath(sheet_path, "output")
                        
                        if not character.three_view_asset:
                            from .models import ImageAsset
                            character.three_view_asset = ImageAsset()
                            
                        from .models import ImageVariant
                        variant = ImageVariant(
                            id=variant_id,
                            url=rel_sheet_path,
                            created_at=time.time(),
                            prompt_used=generation_prompt
                        )
                        character.three_view_asset.variants.insert(0, variant)
                        
                        # Cleanup old variants (keep max 10 non-favorited)
                        cleanup_old_variants(character.three_view_asset)
                        
                        if not character.three_view_asset.selected_id or batch_size == 1:
                            character.three_view_asset.selected_id = variant_id
                            character.three_view_image_url = rel_sheet_path # Legacy sync
                            character.image_url = rel_sheet_path # Legacy mapping
                        
                        successful_generations += 1
                        logger.debug(f"Three view variant {i+1}/{batch_size} generated successfully")
                        
                        if i < batch_size - 1:
                            time.sleep(1)
                    except Exception as e:
                        logger.error(f"Failed to generate three view variant {i+1}/{batch_size}: {e}")
                        continue
                    
                    # Try uploading to OSS if configured - store Object Key (not full URL)
                    try:
                        from ...utils.oss_utils import OSSImageUploader
                        uploader = OSSImageUploader()
                        if uploader.is_configured:
                            object_key = uploader.upload_file(sheet_path, sub_path="assets/characters")
                            if object_key:
                                logger.debug(f"Uploaded three view variant {i+1} to OSS: {object_key}")
                                variant.url = object_key
                                if character.three_view_asset.selected_id == variant.id:
                                    character.three_view_image_url = object_key
                                    character.image_url = object_key
                    except Exception as e:
                        logger.error(f"Failed to upload three view variant {i+1} to OSS: {e}")

                logger.info(f"Three view generation complete: {successful_generations}/{batch_size} variants generated")
                character.three_view_updated_at = time.time()
                
                # Raise exception if all variants failed
                if successful_generations == 0:
                    raise RuntimeError("生成失败，请检查 API 配置或修改描述内容后重试。")

            # 3. Headshot (Derived)
            if generation_type in ["all", "headshot"]:
                if not prompt or generation_type == "all":
                    # Add reference consistency emphasis
                    base_prompt = f"Close-up portrait of the SAME character {character.name}. {character.description}. STRICTLY MAINTAIN the SAME face, hairstyle, skin tone, and facial features as the reference image. Zoom in on face and shoulders, detailed facial features, neutral expression, looking at viewer, high quality, masterpiece."
                else:
                    base_prompt = prompt
                
                # Save the user's prompt WITHOUT style suffix
                character.headshot_prompt = base_prompt
                
                # Generate with style suffix appended
                generation_prompt = f"{base_prompt}, {style_suffix}" if style_suffix and style_suffix not in base_prompt else base_prompt
                
                successful_generations = 0
                for i in range(batch_size):
                    try:
                        variant_id = str(uuid.uuid4())
                        avatar_path = os.path.join(self.output_dir, 'characters', f"{character.id}_avatar_{variant_id}.png")
                        
                        self.model.generate(generation_prompt, avatar_path, ref_image_path=fullbody_path, negative_prompt=negative_prompt, ref_strength=0.8, model_name=i2i_model_name)
                        
                        rel_avatar_path = os.path.relpath(avatar_path, "output")
                        
                        if not character.headshot_asset:
                            from .models import ImageAsset
                            character.headshot_asset = ImageAsset()
                            
                        from .models import ImageVariant
                        variant = ImageVariant(
                            id=variant_id,
                            url=rel_avatar_path,
                            created_at=time.time(),
                            prompt_used=generation_prompt
                        )
                        character.headshot_asset.variants.insert(0, variant)
                        
                        # Cleanup old variants (keep max 10 non-favorited)
                        cleanup_old_variants(character.headshot_asset)
                        
                        if not character.headshot_asset.selected_id or batch_size == 1:
                            character.headshot_asset.selected_id = variant_id
                            character.headshot_image_url = rel_avatar_path # Legacy sync
                            character.avatar_url = rel_avatar_path # Legacy mapping
                        
                        successful_generations += 1
                        logger.debug(f"Headshot variant {i+1}/{batch_size} generated successfully")
                        
                        if i < batch_size - 1:
                            time.sleep(1)
                    except Exception as e:
                        logger.error(f"Failed to generate headshot variant {i+1}/{batch_size}: {e}")
                        continue

                    # Try uploading to OSS if configured - store Object Key (not full URL)
                    try:
                        from ...utils.oss_utils import OSSImageUploader
                        uploader = OSSImageUploader()
                        if uploader.is_configured:
                            object_key = uploader.upload_file(avatar_path, sub_path="assets/characters")
                            if object_key:
                                logger.debug(f"Uploaded headshot variant {i+1} to OSS: {object_key}")
                                variant.url = object_key
                                if character.headshot_asset.selected_id == variant.id:
                                    character.headshot_image_url = object_key
                                    character.avatar_url = object_key
                    except Exception as e:
                        logger.error(f"Failed to upload headshot variant {i+1} to OSS: {e}")

                logger.info(f"Headshot generation complete: {successful_generations}/{batch_size} variants generated")
                character.headshot_updated_at = time.time()
                
                # Raise exception if all variants failed
                if successful_generations == 0:
                    raise RuntimeError("生成失败，请检查 API 配置或修改描述内容后重试。")

            # Update consistency status (Legacy support, but also useful for quick checks)
            if generation_type == "all":
                character.is_consistent = True
            elif character.three_view_updated_at >= character.full_body_updated_at and \
                 character.headshot_updated_at >= character.full_body_updated_at:
                character.is_consistent = True

            character.status = GenerationStatus.COMPLETED
            
        except Exception as e:
            logger.error(f"Failed to generate character {character.name}: {e}")
            character.status = GenerationStatus.FAILED
            raise  # Re-raise to propagate error to caller
            
        return character

    def generate_scene(self, scene: Scene, positive_prompt: str = None, negative_prompt: str = "", batch_size: int = 1, model_name: str = None, size: str = None) -> Scene:
        """Generates a scene reference image."""
        scene.status = GenerationStatus.PROCESSING
        
        # Use provided prompts or fall back to default cinematic style
        if positive_prompt is None:
            positive_prompt = "cinematic lighting, movie still, 8k, highly detailed, realistic"
        
        # Default size for scenes (landscape)
        effective_size = size or "1024*576"
        
        prompt = f"Scene Concept Art: {scene.name}. {scene.description}. High quality, detailed. {positive_prompt}"
        
        try:
            for _ in range(batch_size):
                variant_id = str(uuid.uuid4())
                output_path = os.path.join(self.output_dir, 'scenes', f"{scene.id}_{variant_id}.png")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                
                image_path, _ = self.model.generate(prompt, output_path, negative_prompt=negative_prompt, model_name=model_name, size=effective_size)
                
                rel_path = os.path.relpath(output_path, "output")
                
                if not scene.image_asset:
                    from .models import ImageAsset
                    scene.image_asset = ImageAsset()
                    
                from .models import ImageVariant
                variant = ImageVariant(
                    id=variant_id,
                    url=rel_path,
                    created_at=time.time(),
                    prompt_used=prompt
                )
                scene.image_asset.variants.insert(0, variant)
                
                if not scene.image_asset.selected_id or batch_size == 1:
                    scene.image_asset.selected_id = variant_id
                    scene.image_url = rel_path # Legacy sync

                # Try uploading to OSS if configured - store Object Key (not full URL)
                try:
                    from ...utils.oss_utils import OSSImageUploader
                    uploader = OSSImageUploader()
                    if uploader.is_configured:
                        object_key = uploader.upload_file(output_path, sub_path="assets/scenes")
                        if object_key:
                            logger.debug(f"Uploaded scene variant to OSS: {object_key}")
                            variant.url = object_key
                            if scene.image_asset.selected_id == variant.id:
                                scene.image_url = object_key
                except Exception as e:
                    logger.error(f"Failed to upload scene variant to OSS: {e}")

            scene.status = GenerationStatus.COMPLETED
        except Exception as e:
            logger.error(f"Failed to generate scene {scene.name}: {e}")
            scene.status = GenerationStatus.FAILED
            raise  # Re-raise to propagate error to caller
            
        return scene

    def generate_prop(self, prop: Prop, positive_prompt: str = None, negative_prompt: str = "", batch_size: int = 1, model_name: str = None, size: str = None) -> Prop:
        """Generates a prop reference image."""
        prop.status = GenerationStatus.PROCESSING
        
        # Use provided prompts or fall back to default cinematic style
        if positive_prompt is None:
            positive_prompt = "cinematic lighting, movie still, 8k, highly detailed, realistic"
        
        # Default size for props (square)
        effective_size = size or "1024*1024"
        
        prompt = f"Prop Design: {prop.name}. {prop.description}. Isolated on white background, high quality, detailed. {positive_prompt}"
        
        try:
            for _ in range(batch_size):
                variant_id = str(uuid.uuid4())
                output_path = os.path.join(self.output_dir, 'props', f"{prop.id}_{variant_id}.png")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                
                image_path, _ = self.model.generate(prompt, output_path, negative_prompt=negative_prompt, model_name=model_name, size=effective_size)
                
                rel_path = os.path.relpath(output_path, "output")
                
                if not prop.image_asset:
                    from .models import ImageAsset
                    prop.image_asset = ImageAsset()
                    
                from .models import ImageVariant
                variant = ImageVariant(
                    id=variant_id,
                    url=rel_path,
                    created_at=time.time(),
                    prompt_used=prompt
                )
                prop.image_asset.variants.insert(0, variant)
                
                if not prop.image_asset.selected_id or batch_size == 1:
                    prop.image_asset.selected_id = variant_id
                    prop.image_url = rel_path # Legacy sync

                # Try uploading to OSS if configured - store Object Key (not full URL)
                try:
                    from ...utils.oss_utils import OSSImageUploader
                    uploader = OSSImageUploader()
                    if uploader.is_configured:
                        object_key = uploader.upload_file(output_path, sub_path="assets/props")
                        if object_key:
                            logger.debug(f"Uploaded prop variant to OSS: {object_key}")
                            variant.url = object_key
                            if prop.image_asset.selected_id == variant.id:
                                prop.image_url = object_key
                except Exception as e:
                    logger.error(f"Failed to upload prop variant to OSS: {e}")

            prop.status = GenerationStatus.COMPLETED
        except Exception as e:
            logger.error(f"Failed to generate prop {prop.name}: {e}")
            prop.status = GenerationStatus.FAILED
            raise  # Re-raise to propagate error to caller
            
        return prop
