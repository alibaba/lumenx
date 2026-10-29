import os
import uuid
import time
from types import MappingProxyType
from typing import Callable, Dict, Any, List, Optional
from .models import Character, Scene, Prop, GenerationStatus, ImageAsset, ImageVariant, MAX_VARIANTS_PER_ASSET
from ...models.image import WanxImageModel, ImageGenModel
from ...utils import get_logger
from ...utils.oss_utils import is_object_key
from ...utils.json_storage import StorageError

logger = get_logger(__name__)

THREE_VIEW_NEGATIVE_SUFFIX = ", background, scenery, landscape, shadows, complex background, text, watermark, messy, distorted, extra limbs"


class AssetGenerationOutputError(RuntimeError):
    """Malformed generation output must stop later paid slots, not fall back."""


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
    "3:4": "768*1024",    # Portrait (mild)
    "1:1": "1024*1024",   # Square
    "4:3": "1024*768",    # Landscape (mild)
    "16:9": "1024*576",   # Landscape
}


def prepare_character_reference_sheet(
    name: str,
    description: str,
    *,
    prompt: Optional[str] = None,
    positive_prompt: Optional[str] = None,
    size: Optional[str] = None,
) -> tuple[str, str]:
    """Prepare the legacy reference-sheet prompt and size without side effects."""
    effective_prompt = prompt if prompt else (
        f"Character reference sheet for {name}. {description}. "
        "Multiple views: front, side, back. Clean background, studio lighting."
    )
    if positive_prompt and positive_prompt not in effective_prompt:
        effective_prompt = f"{effective_prompt}, {positive_prompt}"
    return effective_prompt, size or "1024*1024"


def has_full_body_reference_input(character: Character) -> bool:
    """Detect source metadata without changing paid routes when a file is missing."""
    return bool(character.base_character_id) or any(
        variant.is_uploaded_source
        for container in (character.three_view_asset, character.headshot_asset)
        if container is not None for variant in container.variants
    )


def uploaded_full_body_reference(
    character: Character, *, reject_ambiguous: bool = False,
) -> Optional[tuple[str, ImageVariant]]:
    """Keep uploaded three-view before headshot; selection is not this input."""
    for channel, images in (('three_view', character.three_view_asset), ('headshot', character.headshot_asset)):
        if images is None:
            continue
        reference = next((variant for variant in images.variants if variant.is_uploaded_source), None)
        if reference is None:
            continue
        if sum(variant.id == reference.id for variant in images.variants) != 1:
            if reject_ambiguous:
                raise ValueError('studio_full_body_uploaded_reference_ambiguous')
            return None
        return channel, reference
    return None


def prepare_full_body_edit_prompt(prompt: str) -> str:
    """Apply the original reverse-generation instruction exactly once."""
    enhancement = "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, and clothing as the reference image. "
    return prompt if enhancement.strip() in prompt else f"{enhancement}{prompt}"


def prepare_character_full_body(
    name: str,
    description: str,
    *,
    prompt: Optional[str] = None,
    positive_prompt: Optional[str] = None,
    size: Optional[str] = None,
) -> tuple[str, str, str]:
    """Keep the authored prompt distinct from the full-body generation text."""
    base_prompt = prompt or (
        f"Full body character design of {name}, concept art. {description}. "
        "Standing pose, neutral expression, no emotion, looking at viewer. Clean white background, "
        "isolated, no other objects, no scenery, simple background, high quality, masterpiece."
    )
    style = positive_prompt if positive_prompt is not None else "cinematic lighting, movie still, 8k, highly detailed, realistic"
    effective = f"{base_prompt}, {style}" if style and style not in base_prompt else base_prompt
    return base_prompt, effective, size or "576*1024"


def selected_three_view_reference(character: Character, *, reject_ambiguous: bool = False) -> Optional[ImageVariant]:
    """Read one saved choice; current ambiguity cannot erase retained history."""
    images = character.full_body_asset
    if images is None or not images.selected_id:
        return None
    matches = [variant for variant in images.variants if variant.id == images.selected_id]
    if len(matches) > 1:
        if reject_ambiguous:
            raise ValueError('studio_three_view_selected_reference_ambiguous')
        return None
    return matches[0] if matches else None



def derived_image_fallback_reference(
    character: Character, channel: str, *, reject_ambiguous: bool = False,
) -> Optional[tuple[str, Optional[str], str]]:
    """Read the existing derived fallback priority without inventing a variant ID.

    A saved full-body selection belongs to selected-reference validation, even
    when dangling. Upload priority is the first upload, not its selected image.
    """
    if channel not in {'three_view', 'headshot'}:
        raise ValueError('studio_image_fallback_channel_not_supported')
    if character.full_body_asset is not None and character.full_body_asset.selected_id:
        return None
    if character.full_body_image_url:
        return 'full_body_image_url', None, character.full_body_image_url
    sources = (('headshot_upload', character.headshot_asset), ('three_view_upload', character.three_view_asset))
    if channel == 'headshot':
        sources = tuple(reversed(sources))
    for source, images in sources:
        if images is None:
            continue
        reference = next((variant for variant in images.variants if variant.is_uploaded_source), None)
        if reference is None:
            continue
        if sum(variant.id == reference.id for variant in images.variants) != 1:
            if reject_ambiguous:
                raise ValueError(f'studio_{channel}_uploaded_reference_ambiguous')
            return None
        return source, reference.id, reference.url
    return None


def prepare_character_three_view(
    name: str,
    description: str,
    *,
    prompt: Optional[str] = None,
    positive_prompt: Optional[str] = None,
) -> tuple[str, str]:
    """Keep the original three-view template and one-time style suffix."""
    base_prompt = prompt or (
        f"Character Reference Sheet for {name}. {description}. Three-view character design: "
        "Front view, Side view, and Back view. STRICTLY MAINTAIN the SAME character appearance, "
        "face, hairstyle, and clothing as the reference image. Full body, standing pose, neutral "
        "expression. Consistent clothing and details across all views. Simple white background, "
        "clean lines, studio lighting, high quality."
    )
    style = positive_prompt if positive_prompt is not None else "cinematic lighting, movie still, 8k, highly detailed, realistic"
    effective = f"{base_prompt}, {style}" if style and style not in base_prompt else base_prompt
    return base_prompt, effective


def prepare_character_headshot(
    name: str,
    description: str,
    *,
    prompt: Optional[str] = None,
    positive_prompt: Optional[str] = None,
) -> tuple[str, str]:
    """Keep the original portrait template and one-time style suffix."""
    base_prompt = prompt or (
        f"Close-up portrait of the SAME character {name}. {description}. "
        "STRICTLY MAINTAIN the SAME face, hairstyle, skin tone, and facial features "
        "as the reference image. Zoom in on face and shoulders, detailed facial features, "
        "neutral expression, looking at viewer, high quality, masterpiece."
    )
    style = positive_prompt if positive_prompt is not None else "cinematic lighting, movie still, 8k, highly detailed, realistic"
    effective = f"{base_prompt}, {style}" if style and style not in base_prompt else base_prompt
    return base_prompt, effective


def prepare_scene_image(
    name: str,
    description: str,
    *,
    prompt: Optional[str] = None,
    positive_prompt: Optional[str] = None,
    size: Optional[str] = None,
) -> tuple[str, str]:
    """Preserve authored text, or use the legacy scene template when absent."""
    if positive_prompt is None:
        positive_prompt = "cinematic lighting, movie still, 8k, highly detailed, realistic"
    if prompt:
        if positive_prompt and positive_prompt not in prompt:
            prompt = f"{prompt}, {positive_prompt}"
    else:
        prompt = f"Scene Concept Art: {name}. {description}. High quality, detailed. {positive_prompt}"
    return prompt, size or "1024*576"


def prepare_prop_image(
    name: str,
    description: str,
    *,
    prompt: Optional[str] = None,
    positive_prompt: Optional[str] = None,
    size: Optional[str] = None,
) -> tuple[str, str]:
    """Preserve authored text, or use the legacy prop template when absent."""
    if positive_prompt is None:
        positive_prompt = "cinematic lighting, movie still, 8k, highly detailed, realistic"
    if prompt:
        if positive_prompt and positive_prompt not in prompt:
            prompt = f"{prompt}, {positive_prompt}"
    else:
        prompt = f"Prop Design: {name}. {description}. Isolated on white background, high quality, detailed. {positive_prompt}"
    return prompt, size or "1024*1024"


class AssetGenerator:
    def __init__(self, config: Dict[str, Any] = None, *, before_generation: Optional[Callable[[], None]] = None, env=None):
        self.config = config or {}
        self._env = None if env is None else MappingProxyType(dict(env))
        self.before_generation = before_generation
        self.output_dir = self.config.get('output_dir', 'output/assets')
        self.output_root = self.config.get('output_root') or _default_output_root(self.output_dir, "assets")
        self.model_config = dict(self.config.get('model') or {})
        self.model_config.setdefault('output_root', self.output_root)
        self.model = WanxImageModel(self.model_config, **({"env": self._env} if self._env is not None else {}))
        self._mulerouter_image_model = None

    def _check_generation_allowed(self, check_current: Optional[Callable[[], None]] = None) -> None:
        if self.before_generation is not None:
            self.before_generation()
        if check_current is not None:
            check_current()

    def _generate(self, prompt: str, output_path: str, *, check_current=None, **kwargs):
        """Check runtime storage around each individual paid generation.

        The second check keeps a late result from mutating a failed runtime.
        The generated file remains intact for recovery after restart.
        """
        self._check_generation_allowed(check_current)
        try:
            result = self._get_model_for(kwargs.get("model_name")).generate(prompt, output_path, **kwargs)
        except StorageError:
            raise
        except Exception:
            self._check_generation_allowed(check_current)
            raise
        self._check_generation_allowed(check_current)
        return result

    def _upload_file(self, uploader, path: str, *, sub_path: str, check_current=None):
        """Do not publish a remote URL after the owning runtime stops writing."""
        self._check_generation_allowed(check_current)
        try:
            result = uploader.upload_file(path, sub_path=sub_path)
        except StorageError:
            raise
        except Exception:
            # Optional mirror errors remain recoverable only while the local
            # runtime is still writable, including the last upload in a batch.
            self._check_generation_allowed(check_current)
            raise
        self._check_generation_allowed(check_current)
        if result is not None:
            if not isinstance(result, str):
                raise AssetGenerationOutputError("Asset upload returned a non-text reference")
            try:
                result.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise AssetGenerationOutputError("Asset upload returned invalid UTF-8 text") from exc
        return result

    def _rel_output_path(self, path: str) -> str:
        return os.path.relpath(path, self.output_root)

    def _resolve_output_ref(self, value: str) -> Optional[str]:
        return _resolve_output_ref(self.output_root, value)

    def _get_model_for(self, model_name: str) -> "ImageGenModel":
        """Route to the correct image adapter based on model name."""
        if model_name and model_name.startswith("gpt-image"):
            if self._mulerouter_image_model is None:
                from ...models.mulerouter import MuleRouterImageModel
                self._mulerouter_image_model = MuleRouterImageModel(self.model_config, **({"env": self._env} if self._env is not None else {}))
            return self._mulerouter_image_model
        return self.model

    def generate_character(self, character: Character, generation_type: str = "all", prompt: str = "", positive_prompt: str = None, negative_prompt: str = "", batch_size: int = 1, model_name: str = None, i2i_model_name: str = None, size: str = None, *, check_current: Optional[Callable[[], None]] = None) -> Character:
        """
        Generates character assets based on generation_type.
        Types: 'full_body', 'three_view', 'headshot', 'all'
        """
        self._check_generation_allowed(check_current)
        character.status = GenerationStatus.PROCESSING
        
        # Default style suffix if not provided (None means use default, "" means no style)
        style_suffix = positive_prompt if positive_prompt is not None else "cinematic lighting, movie still, 8k, highly detailed, realistic"
        
        # Default size if not provided
        effective_size = size or "576*1024"  # Default to portrait for characters
        
        try:
            # === R2V: Single unified reference sheet (T2I only) ===
            if generation_type == "reference_sheet":
                effective_prompt, effective_size = prepare_character_reference_sheet(
                    character.name, character.description,
                    prompt=prompt, positive_prompt=positive_prompt, size=size,
                )

                successful_generations = 0
                last_error = ""
                for i in range(batch_size):
                    try:
                        variant_id = str(uuid.uuid4())
                        sheet_path = os.path.join(self.output_dir, 'characters', f"{character.id}_refsheet_{variant_id}.png")
                        os.makedirs(os.path.dirname(sheet_path), exist_ok=True)

                        self._generate(
                            effective_prompt, sheet_path,
                            check_current=check_current,
                            negative_prompt=negative_prompt,
                            model_name=model_name,
                            size=effective_size
                        )

                        rel_path = self._rel_output_path(sheet_path)

                        if not character.reference_sheet:
                            from .models import AssetUnit
                            character.reference_sheet = AssetUnit()

                        from .models import ImageVariant
                        variant = ImageVariant(
                            id=variant_id,
                            url=rel_path,
                            created_at=time.time(),
                            prompt_used=effective_prompt,
                        )
                        character.reference_sheet.image_variants.append(variant)

                        if not character.reference_sheet.selected_image_id:
                            character.reference_sheet.selected_image_id = variant_id
                            character.image_url = rel_path

                        successful_generations += 1

                        # Upload to OSS if configured
                        try:
                            from ...utils.oss_utils import OSSImageUploader
                            uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                            if uploader.is_configured:
                                object_key = self._upload_file(uploader, sheet_path, sub_path="assets/characters", check_current=check_current)
                                if object_key:
                                    variant.url = object_key
                                    if character.reference_sheet.selected_image_id == variant_id:
                                        character.image_url = object_key
                        except (StorageError, AssetGenerationOutputError):
                            raise
                        except Exception as e:
                            logger.error(f"Failed to upload reference sheet to OSS: {e}")

                        if i < batch_size - 1:
                            time.sleep(1)
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"Failed to generate reference sheet variant {i+1}/{batch_size}: {e}")
                        continue

                if successful_generations == 0:
                    raise RuntimeError(f"生成失败：{last_error}")

                character.status = GenerationStatus.COMPLETED
                return character

            # 1. Full Body (Master)
            if generation_type in ["all", "full_body"]:
                base_prompt, generation_prompt, effective_size = prepare_character_full_body(
                    character.name, character.description, prompt=prompt,
                    positive_prompt=positive_prompt, size=size,
                )
                character.full_body_prompt = base_prompt
                
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
                            if is_object_key(ref_url, env=self._env):
                                ref_image_path = ref_url
                                logger.debug(f"Reverse generation: Using uploaded three_views as reference: {ref_url}")
                            else:
                                local_path = self._resolve_output_ref(ref_url)
                                if local_path and os.path.exists(local_path):
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
                            if is_object_key(ref_url, env=self._env):
                                ref_image_path = ref_url
                                logger.debug(f"Reverse generation: Using uploaded headshot as reference: {ref_url}")
                            else:
                                local_path = self._resolve_output_ref(ref_url)
                                if local_path and os.path.exists(local_path):
                                    ref_image_path = local_path
                                    logger.debug(f"Reverse generation: Using local headshot as reference: {local_path}")

                # Batch Generation Loop
                successful_generations = 0
                last_error = ""
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
                            effective_model_name = i2i_model_name or "wan2.6-image"
                            logger.debug(f"Reverse generation: Using I2I model {effective_model_name} with reference image")
                            
                            # Enhance prompt for reverse generation to emphasize reference consistency (only if not already present)
                            effective_generation_prompt = prepare_full_body_edit_prompt(generation_prompt)
                        
                        self._generate(effective_generation_prompt, fullbody_path, ref_image_path=ref_image_path, negative_prompt=negative_prompt, model_name=effective_model_name, size=effective_size, check_current=check_current)
                        
                        rel_fullbody_path = self._rel_output_path(fullbody_path)
                        
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
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"Failed to generate full body variant {i+1}/{batch_size}: {e}")
                        continue

                    try:
                        from ...utils.oss_utils import OSSImageUploader
                        uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                        if uploader.is_configured:
                            object_key = self._upload_file(uploader, fullbody_path, sub_path="assets/characters", check_current=check_current)
                            if object_key:
                                logger.debug(f"Uploaded full body variant {i+1} to OSS: {object_key}")
                                variant.url = object_key
                                if character.full_body_asset.selected_id == variant.id:
                                    character.full_body_image_url = object_key
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        logger.error(f"Failed to upload full body variant {i+1} to OSS: {e}")

                logger.info(f"Full body generation complete: {successful_generations}/{batch_size} variants generated")
                character.full_body_updated_at = time.time()

                if successful_generations == 0:
                    raise RuntimeError(f"生成失败：{last_error}")
                
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
                if is_object_key(reference_url, env=self._env):
                    # OSS Object Key - pass directly, image.py will handle signing
                    fullbody_path = reference_url
                    logger.debug(f"Using OSS Object Key for reference: {reference_url}")
                else:
                    # Local relative path - prepend output directory
                    fullbody_path = self._resolve_output_ref(reference_url)
                    logger.debug(f"Using local path for reference: {fullbody_path}")
            else:
                fullbody_path = None

            # 2. Three View Sheet (Derived)
            if generation_type in ["all", "three_view"]:
                base_prompt, generation_prompt = prepare_character_three_view(
                    character.name, character.description,
                    prompt=None if generation_type == "all" else prompt,
                    positive_prompt=style_suffix,
                )
                
                # Save the user's prompt WITHOUT style suffix
                character.three_view_prompt = base_prompt
                
                sheet_negative = negative_prompt + THREE_VIEW_NEGATIVE_SUFFIX

                successful_generations = 0
                last_error = ""
                for i in range(batch_size):
                    try:
                        variant_id = str(uuid.uuid4())
                        sheet_path = os.path.join(self.output_dir, 'characters', f"{character.id}_sheet_{variant_id}.png")
                        
                        self._generate(generation_prompt, sheet_path, ref_image_path=fullbody_path, negative_prompt=sheet_negative, ref_strength=0.8, model_name=i2i_model_name, check_current=check_current)
                        
                        rel_sheet_path = self._rel_output_path(sheet_path)
                        
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
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"Failed to generate three view variant {i+1}/{batch_size}: {e}")
                        continue
                    
                    # Try uploading to OSS if configured - store Object Key (not full URL)
                    try:
                        from ...utils.oss_utils import OSSImageUploader
                        uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                        if uploader.is_configured:
                            object_key = self._upload_file(uploader, sheet_path, sub_path="assets/characters", check_current=check_current)
                            if object_key:
                                logger.debug(f"Uploaded three view variant {i+1} to OSS: {object_key}")
                                variant.url = object_key
                                if character.three_view_asset.selected_id == variant.id:
                                    character.three_view_image_url = object_key
                                    character.image_url = object_key
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        logger.error(f"Failed to upload three view variant {i+1} to OSS: {e}")

                logger.info(f"Three view generation complete: {successful_generations}/{batch_size} variants generated")
                character.three_view_updated_at = time.time()
                
                # Raise exception if all variants failed
                if successful_generations == 0:
                    raise RuntimeError(f"生成失败：{last_error}")

            # 3. Headshot (Derived)
            if generation_type in ["all", "headshot"]:
                base_prompt, generation_prompt = prepare_character_headshot(
                    character.name, character.description,
                    prompt=prompt if generation_type != "all" else None,
                    positive_prompt=style_suffix,
                )
                # Save the user's prompt WITHOUT style suffix.
                character.headshot_prompt = base_prompt

                successful_generations = 0
                last_error = ""
                for i in range(batch_size):
                    try:
                        variant_id = str(uuid.uuid4())
                        avatar_path = os.path.join(self.output_dir, 'characters', f"{character.id}_avatar_{variant_id}.png")
                        
                        self._generate(generation_prompt, avatar_path, ref_image_path=fullbody_path, negative_prompt=negative_prompt, ref_strength=0.8, model_name=i2i_model_name, check_current=check_current)
                        
                        rel_avatar_path = self._rel_output_path(avatar_path)
                        
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
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"Failed to generate headshot variant {i+1}/{batch_size}: {e}")
                        continue

                    # Try uploading to OSS if configured - store Object Key (not full URL)
                    try:
                        from ...utils.oss_utils import OSSImageUploader
                        uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                        if uploader.is_configured:
                            object_key = self._upload_file(uploader, avatar_path, sub_path="assets/characters", check_current=check_current)
                            if object_key:
                                logger.debug(f"Uploaded headshot variant {i+1} to OSS: {object_key}")
                                variant.url = object_key
                                if character.headshot_asset.selected_id == variant.id:
                                    character.headshot_image_url = object_key
                                    character.avatar_url = object_key
                    except (StorageError, AssetGenerationOutputError):
                        raise
                    except Exception as e:
                        logger.error(f"Failed to upload headshot variant {i+1} to OSS: {e}")

                logger.info(f"Headshot generation complete: {successful_generations}/{batch_size} variants generated")
                character.headshot_updated_at = time.time()
                
                # Raise exception if all variants failed
                if successful_generations == 0:
                    raise RuntimeError(f"生成失败：{last_error}")

            # Update consistency status (Legacy support, but also useful for quick checks)
            if generation_type == "all":
                character.is_consistent = True
            elif character.three_view_updated_at >= character.full_body_updated_at and \
                 character.headshot_updated_at >= character.full_body_updated_at:
                character.is_consistent = True

            character.status = GenerationStatus.COMPLETED
            
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate character {character.name}: {e}")
            character.status = GenerationStatus.FAILED
            raise  # Re-raise to propagate error to caller
            
        return character

    def generate_scene(self, scene: Scene, positive_prompt: str = None, negative_prompt: str = "", batch_size: int = 1, model_name: str = None, size: str = None, *, prompt: Optional[str] = None, check_current: Optional[Callable[[], None]] = None) -> Scene:
        """Generates a scene reference image."""
        self._check_generation_allowed(check_current)
        scene.status = GenerationStatus.PROCESSING
        
        prompt, effective_size = prepare_scene_image(
            scene.name, scene.description, prompt=prompt, positive_prompt=positive_prompt, size=size,
        )
        
        try:
            for _ in range(batch_size):
                variant_id = str(uuid.uuid4())
                output_path = os.path.join(self.output_dir, 'scenes', f"{scene.id}_{variant_id}.png")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                
                image_path, _ = self._generate(prompt, output_path, negative_prompt=negative_prompt, model_name=model_name, size=effective_size, check_current=check_current)
                
                rel_path = self._rel_output_path(output_path)
                
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
                    uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                    if uploader.is_configured:
                        object_key = self._upload_file(uploader, output_path, sub_path="assets/scenes", check_current=check_current)
                        if object_key:
                            logger.debug(f"Uploaded scene variant to OSS: {object_key}")
                            variant.url = object_key
                            if scene.image_asset.selected_id == variant.id:
                                scene.image_url = object_key
                except (StorageError, AssetGenerationOutputError):
                    raise
                except Exception as e:
                    logger.error(f"Failed to upload scene variant to OSS: {e}")

            scene.status = GenerationStatus.COMPLETED
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate scene {scene.name}: {e}")
            scene.status = GenerationStatus.FAILED
            raise  # Re-raise to propagate error to caller
            
        return scene

    def generate_prop(self, prop: Prop, positive_prompt: str = None, negative_prompt: str = "", batch_size: int = 1, model_name: str = None, size: str = None, *, prompt: Optional[str] = None, check_current: Optional[Callable[[], None]] = None) -> Prop:
        """Generates a prop reference image."""
        self._check_generation_allowed(check_current)
        prop.status = GenerationStatus.PROCESSING
        
        prompt, effective_size = prepare_prop_image(
            prop.name, prop.description, prompt=prompt, positive_prompt=positive_prompt, size=size,
        )
        
        try:
            for _ in range(batch_size):
                variant_id = str(uuid.uuid4())
                output_path = os.path.join(self.output_dir, 'props', f"{prop.id}_{variant_id}.png")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                
                image_path, _ = self._generate(prompt, output_path, negative_prompt=negative_prompt, model_name=model_name, size=effective_size, check_current=check_current)
                
                rel_path = self._rel_output_path(output_path)
                
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
                    uploader = OSSImageUploader(**({"env": self._env, "output_root": self.output_root} if self._env is not None else {}))
                    if uploader.is_configured:
                        object_key = self._upload_file(uploader, output_path, sub_path="assets/props", check_current=check_current)
                        if object_key:
                            logger.debug(f"Uploaded prop variant to OSS: {object_key}")
                            variant.url = object_key
                            if prop.image_asset.selected_id == variant.id:
                                prop.image_url = object_key
                except (StorageError, AssetGenerationOutputError):
                    raise
                except Exception as e:
                    logger.error(f"Failed to upload prop variant to OSS: {e}")

            prop.status = GenerationStatus.COMPLETED
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate prop {prop.name}: {e}")
            prop.status = GenerationStatus.FAILED
            raise  # Re-raise to propagate error to caller
            
        return prop
