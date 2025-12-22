from typing import Dict, Any, List, Optional, Tuple
import json
import os
import time
import uuid
import subprocess
import threading
from urllib.parse import quote
from .models import Script, GenerationStatus, VideoTask
from .llm import ScriptProcessor
from .assets import AssetGenerator
from .storyboard import StoryboardGenerator
from .video import VideoGenerator
from .audio import AudioGenerator
from .export import ExportManager
from ...utils import get_logger

logger = get_logger(__name__)

class ComicGenPipeline:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.script_processor = ScriptProcessor()
        self.asset_generator = AssetGenerator(self.config.get('assets'))
        self.storyboard_generator = StoryboardGenerator(self.config.get('storyboard'))
        self.video_generator = VideoGenerator(self.config.get('video'))
        self.audio_generator = AudioGenerator(self.config.get('audio'))
        self.export_manager = ExportManager(self.config.get('export'))
        
        self.data_file = "output/projects.json"
        self._save_lock = threading.Lock()  # Lock to prevent concurrent file writes
        self.scripts: Dict[str, Script] = self._load_data()

    # ... (existing methods)

    def export_project(self, script_id: str, options: Dict[str, Any]) -> str:
        """Step 7: Export project to final video."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        export_url = self.export_manager.render_project(script, options)
        return export_url

    def get_script(self, script_id: str) -> Optional[Script]:
        return self.scripts.get(script_id)

    def _load_data(self) -> Dict[str, Script]:
        if not os.path.exists(self.data_file):
            return {}
        try:
            with open(self.data_file, 'r') as f:
                data = json.load(f)
                return {k: Script(**v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            return {}

    def _save_data(self):
        """Save data with thread lock to prevent concurrent write issues."""
        with self._save_lock:
            try:
                os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
                with open(self.data_file, 'w') as f:
                    json.dump({k: v.dict() for k, v in self.scripts.items()}, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save data: {e}")

    def create_project(self, title: str, text: str, skip_analysis: bool = False) -> Script:
        """Step 1: Parse novel and create project."""
        if skip_analysis:
            script = self.script_processor.create_draft_script(title, text)
        else:
            script = self.script_processor.parse_novel(title, text)
            
        self.scripts[script.id] = script
        self._save_data()
        return script
    
    def reparse_project(self, script_id: str, text: str) -> Script:
        """Re-parse the text for an existing project, replacing all entities."""
        existing_script = self.scripts.get(script_id)
        if not existing_script:
            raise ValueError("Script not found")
        
        # Parse the new text (this generates new entities with new IDs)
        new_script = self.script_processor.parse_novel(existing_script.title, text)
        
        # Preserve the original script ID and timestamps
        new_script.id = existing_script.id
        new_script.created_at = existing_script.created_at
        new_script.updated_at = time.time()
        
        # Replace the script in memory
        self.scripts[script_id] = new_script
        self._save_data()
        return new_script


    def generate_assets(self, script_id: str) -> Script:
        """Step 2: Generate character and scene assets (Batch)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        logger.info(f"Generating assets for script {script.id}")
        
        # Sort characters: Base characters first (those without base_character_id)
        sorted_chars = sorted(script.characters, key=lambda c: 0 if not c.base_character_id else 1)

        for char in sorted_chars:
            self.generate_asset(script_id, char.id, "character")
            
        for scene in script.scenes:
            self.generate_asset(script_id, scene.id, "scene")
            
        for prop in script.props:
            self.generate_asset(script_id, prop.id, "prop")
            
        self._save_data()
        return script

    def generate_asset(self, script_id: str, asset_id: str, asset_type: str, style_preset: str = None, reference_image_url: str = None, style_prompt: str = None, generation_type: str = "all", prompt: str = None, apply_style: bool = True, negative_prompt: str = None, batch_size: int = 1, model_name: str = None) -> Script:
        """Step 2: Generate a specific asset (character/scene/prop).
        If style_preset is None, uses the project's global style."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        
        # Get effective model name from project settings if not overridden
        effective_model_name = model_name or script.model_settings.t2i_model
        
        # Get effective size based on asset type
        from .assets import ASPECT_RATIO_TO_SIZE
        if asset_type == "character":
            aspect_ratio = script.model_settings.character_aspect_ratio
            default_size = "576*1024"  # Portrait
        elif asset_type == "scene":
            aspect_ratio = script.model_settings.scene_aspect_ratio
            default_size = "1024*576"  # Landscape
        elif asset_type == "prop":
            aspect_ratio = script.model_settings.prop_aspect_ratio
            default_size = "1024*1024"  # Square
        else:
            aspect_ratio = "9:16"
            default_size = "576*1024"
        
        effective_size = ASPECT_RATIO_TO_SIZE.get(aspect_ratio, default_size)
        
        # Determine effective style: Art Direction > passed style > legacy style
        effective_positive_prompt = ""
        effective_negative_prompt = negative_prompt or "" # Use passed negative prompt if available
        
        # Only calculate style prompt if apply_style is True
        if apply_style:
            if script.art_direction and script.art_direction.style_config:
                # Use Art Direction (highest priority)
                effective_positive_prompt = script.art_direction.style_config.get('positive_prompt', '')
                # Append global negative prompt if not overridden or append to it?
                # Let's append global negative prompt to the specific one for better results
                global_neg = script.art_direction.style_config.get('negative_prompt', '')
                if global_neg:
                    effective_negative_prompt = f"{effective_negative_prompt}, {global_neg}" if effective_negative_prompt else global_neg
            elif style_prompt:
                # Use passed style_prompt (for manual override)
                effective_positive_prompt = style_prompt
            elif style_preset:
                # Use passed style_preset (legacy)
                effective_positive_prompt = f"{style_preset} style"
            elif script.style_preset:
                # Fallback to script's legacy style_preset
                effective_positive_prompt = f"{script.style_preset} style"
                if script.style_prompt:
                    effective_positive_prompt += f", {script.style_prompt}"
        
        asset_list = []
        target_asset = None
        
        if asset_type == "character":
            asset_list = script.characters
        elif asset_type == "scene":
            asset_list = script.scenes
        elif asset_type == "prop":
            asset_list = script.props
        else:
            raise ValueError(f"Invalid asset_type: {asset_type}")
        
        target_asset = next((a for a in asset_list if a.id == asset_id), None)
        if not target_asset:
            raise ValueError(f"{asset_type.capitalize()} {asset_id} not found")
        
        target_asset.status = GenerationStatus.PROCESSING
        self._save_data()
        
        try:
            # Generate with Art Direction style injected
            if asset_type == "character":
                # Pass generation_type and specific prompt if available
                # If prompt is provided (from Workbench), use it directly. 
                # Otherwise, asset_generator will construct it using effective_positive_prompt.
                # Note: If prompt is provided, we might still want to append style if it's not included?
                # For now, let's assume the Workbench passes the FULL prompt or we pass style separately.
                # The asset_generator.generate_character expects 'prompt' as the specific prompt.
                # If 'prompt' is None, it constructs one.
                # We should pass effective_positive_prompt as 'positive_prompt' (style suffix) to be appended if needed.
                self.asset_generator.generate_character(
                    target_asset, 
                    generation_type=generation_type, 
                    prompt=prompt, 
                    positive_prompt=effective_positive_prompt, # Used as style suffix if prompt is auto-generated
                    negative_prompt=effective_negative_prompt,
                    batch_size=batch_size,
                    model_name=effective_model_name,
                    size=effective_size
                )
            elif asset_type == "scene":
                self.asset_generator.generate_scene(target_asset, effective_positive_prompt, effective_negative_prompt, batch_size=batch_size, model_name=effective_model_name, size=effective_size)
            elif asset_type == "prop":
                self.asset_generator.generate_prop(target_asset, effective_positive_prompt, effective_negative_prompt, batch_size=batch_size, model_name=effective_model_name, size=effective_size)
                
            target_asset.status = GenerationStatus.COMPLETED
        except Exception as e:
            target_asset.status = GenerationStatus.FAILED
            raise e
        finally:
            self._save_data()
        
        return script
    
    def toggle_asset_lock(self, script_id: str, asset_id: str, asset_type: str) -> Script:
        """Toggle the locked status of an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
            
        # Toggle the locked status
        target_asset.locked = not target_asset.locked
        self._save_data()
        return script

    def toggle_frame_lock(self, script_id: str, frame_id: str) -> Script:
        """Toggle the locked status of a frame."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        target_frame = next((f for f in script.frames if f.id == frame_id), None)
        if not target_frame:
            raise ValueError(f"Frame {frame_id} not found")
            
        # Toggle the locked status
        target_frame.locked = not target_frame.locked
        self._save_data()
        return script

    def update_asset_image(self, script_id: str, asset_id: str, asset_type: str, image_url: str) -> Script:
        """Updates the image URL of an asset manually."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
            
        target_asset.image_url = image_url
        # For characters, also update avatar if it's not set or if we want to sync them
        # For now, let's assume the uploaded image is the main reference. 
        # If it's a character, we might want to set avatar_url to the same image for simplicity
        if asset_type == "character":
            target_asset.avatar_url = image_url
            
        self._save_data()
        return script

    def update_asset_description(self, script_id: str, asset_id: str, asset_type: str, description: str) -> Script:
        """Updates the description of an asset."""
        return self.update_asset_attributes(script_id, asset_id, asset_type, {"description": description})

    def update_asset_attributes(self, script_id: str, asset_id: str, asset_type: str, attributes: Dict[str, Any]) -> Script:
        """Updates arbitrary attributes of an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            
        if not target_asset:
            raise ValueError(f"Asset {asset_id} of type {asset_type} not found")
            
        # Update attributes
        for key, value in attributes.items():
            if hasattr(target_asset, key):
                setattr(target_asset, key, value)
            else:
                logger.warning(f"Attribute {key} not found in {asset_type} model")
        
        self._save_data()
        return script

    def update_project_style(self, script_id: str, style_preset: str, style_prompt: Optional[str] = None) -> Script:
        """Updates the global style settings for a project."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        script.style_preset = style_preset
        script.style_prompt = style_prompt
        script.updated_at = time.time()
        self._save_data()
        return script
    
    def save_art_direction(self, script_id: str, selected_style_id: str, style_config: Dict[str, Any], custom_styles: List[Dict[str, Any]] = None, ai_recommendations: List[Dict[str, Any]] = None) -> Script:
        """Saves the Art Direction configuration."""
        from .models import ArtDirection
        
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        
        # Create Art Direction object
        art_direction = ArtDirection(
            selected_style_id=selected_style_id,
            style_config=style_config,
            custom_styles=custom_styles or [],
            ai_recommendations=ai_recommendations or []
        )
        
        script.art_direction = art_direction
        script.updated_at = time.time()
        self._save_data()
        return script


    def generate_storyboard(self, script_id: str) -> Script:
        """Step 3: Generate storyboard images (Initial/Batch)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        script = self.storyboard_generator.generate_storyboard(script)
        self._save_data()
        return script

    def update_frame(self, script_id: str, frame_id: str, **kwargs) -> Script:
        """Update frame data (prompt, scene_id, character_ids, etc.)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        
        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")
        
        # Update only provided fields
        if kwargs.get('image_prompt') is not None:
            frame.image_prompt = kwargs['image_prompt']
        if kwargs.get('action_description') is not None:
            frame.action_description = kwargs['action_description']
        if kwargs.get('dialogue') is not None:
            frame.dialogue = kwargs['dialogue']
        if kwargs.get('camera_angle') is not None:
            frame.camera_angle = kwargs['camera_angle']
        if kwargs.get('scene_id') is not None:
            frame.scene_id = kwargs['scene_id']
        if kwargs.get('character_ids') is not None:
            frame.character_ids = kwargs['character_ids']
        
        self._save_data()
        return script

    def generate_storyboard_render(self, script_id: str, frame_id: str, composition_data: Optional[Dict[str, Any]], prompt: str, batch_size: int = 1) -> Script:
        """Step 3b: Render a specific frame from composition data."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError(f"Frame {frame_id} not found")
            
        frame.status = GenerationStatus.PROCESSING
        if composition_data:
            frame.composition_data = composition_data
        frame.image_prompt = prompt
        self._save_data()
        
        try:
            # Extract reference image URL from composition data if available
            ref_image_url = None
            ref_image_urls = []
            
            if composition_data:
                ref_image_url = composition_data.get('reference_image_url')
                ref_image_urls = composition_data.get('reference_image_urls', [])
            
            ref_image_path = None
            ref_image_paths = []
            
            # Resolve single path
            if ref_image_url:
                potential_path = os.path.join("output", ref_image_url)
                if os.path.exists(potential_path):
                    ref_image_path = os.path.abspath(potential_path)
            
            # Resolve multiple paths
            for url in ref_image_urls:
                potential_path = os.path.join("output", url)
                if os.path.exists(potential_path):
                    ref_image_paths.append(os.path.abspath(potential_path))
            
            # Use the prompt as-is from frontend (already contains style)
            final_prompt = prompt
            
            # Update frame with final prompt
            frame.image_prompt = final_prompt
            
            # Find scene for this frame
            scene = next((s for s in script.scenes if s.id == frame.scene_id), None)

            # Get effective size from storyboard_aspect_ratio
            from .assets import ASPECT_RATIO_TO_SIZE
            storyboard_aspect_ratio = script.model_settings.storyboard_aspect_ratio
            effective_size = ASPECT_RATIO_TO_SIZE.get(storyboard_aspect_ratio, "1024*576")  # Default to landscape

            # Call generator
            self.storyboard_generator.generate_frame(
                frame, 
                script.characters, 
                scene, 
                ref_image_path=ref_image_path,
                ref_image_paths=ref_image_paths,
                prompt=final_prompt,
                batch_size=batch_size,
                size=effective_size
            )
            
            self._save_data()
            return script
        except Exception as e:
            frame.status = GenerationStatus.FAILED
            self._save_data()
            raise e
            # 1. Take the composition_data (positions of assets)
            # 2. Construct a composite image (ControlNet input)
            # 3. Call Img2Img with the composite + prompt
            
            logger.info(f"Rendering frame {frame_id} with prompt: {prompt}")
            time.sleep(1.5) # Simulate processing
            
            # Mock Result
            mock_url = f"https://placehold.co/1280x720/2a2a2a/FFF?text=Rendered+Frame+{frame_id}"
            frame.rendered_image_url = mock_url
            frame.image_url = mock_url # Update main image too
            frame.status = GenerationStatus.COMPLETED
            
        except Exception as e:
            logger.error(f"Frame rendering failed: {e}")
            frame.status = GenerationStatus.FAILED
            
        self._save_data()
        return script

    def generate_video(self, script_id: str) -> Script:
        """Step 4: Generate video clips."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        script = self.video_generator.generate_video(script)
        self._save_data()
        return script

    def create_video_task(self, script_id: str, image_url: str, prompt: str, duration: int = 5, seed: int = None, resolution: str = "720p", generate_audio: bool = False, audio_url: str = None, prompt_extend: bool = True, negative_prompt: str = None, model: str = "wan2.6-i2v", frame_id: str = None, shot_type: str = "single") -> Tuple[Script, str]:
        """Creates a new video generation task."""
        script = self.get_script(script_id)
        if not script:
            raise ValueError("Script not found")
        
        task_id = str(uuid.uuid4())
        
        # Snapshot the input image to ensure consistency
        snapshot_url = image_url
        try:
            # Resolve source path
            if not image_url.startswith("http"):
                # Assume relative to output dir
                src_path = os.path.join("output", image_url)
                if os.path.exists(src_path):
                    # Create snapshot dir
                    snapshot_dir = os.path.join("output", "video_inputs")
                    os.makedirs(snapshot_dir, exist_ok=True)
                    
                    # Define snapshot path
                    ext = os.path.splitext(image_url)[1] or ".png"
                    snapshot_filename = f"{task_id}{ext}"
                    snapshot_path = os.path.join(snapshot_dir, snapshot_filename)
                    
                    # Copy file
                    import shutil
                    shutil.copy2(src_path, snapshot_path)
                    
                    # Update URL to relative path
                    snapshot_url = f"video_inputs/{snapshot_filename}"
        except Exception as e:
            logger.error(f"Failed to snapshot input image: {e}")
            # Fallback to original URL

        task = VideoTask(
            id=task_id,
            project_id=script_id,
            frame_id=frame_id,
            image_url=snapshot_url,
            prompt=prompt,
            status="pending",
            duration=duration,
            seed=seed,
            resolution=resolution,
            generate_audio=generate_audio,
            audio_url=audio_url,
            prompt_extend=prompt_extend,
            negative_prompt=negative_prompt,
            model=model,
            shot_type=shot_type,  # Store shot_type for wan2.6-i2v
            created_at=time.time()
        )
        
        if not script.video_tasks:
            script.video_tasks = []
        script.video_tasks.append(task)
        
        self._save_data()
        return script, task_id
        return script, task_id

    def _download_temp_image(self, url: str) -> str:
        """Downloads an image to a temporary file."""
        import requests
        import tempfile
        
        # If it's a local file path (relative to output)
        if not url.startswith("http"):
            local_path = os.path.join("output", url)
            if os.path.exists(local_path):
                return local_path
            # Try absolute path if it exists
            if os.path.exists(url):
                return url
                
        # Download from URL
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            # Create temp file
            fd, path = tempfile.mkstemp(suffix=".png")
            with os.fdopen(fd, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return path
        except Exception as e:
            logger.error(f"Failed to download image: {e}")
            raise
    def select_video_for_frame(self, script_id: str, frame_id: str, video_id: str) -> Script:
        """Step 5a: Select a video variant for a frame."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")
            
        # Verify video exists and belongs to project
        video = next((v for v in script.video_tasks if v.id == video_id), None)
        if not video:
            raise ValueError("Video task not found")
            
        frame.selected_video_id = video_id
        
        # Also update the frame's video_url to point to this video for easy access
        frame.video_url = video.video_url
        
        self._save_data()
        return script

    def merge_videos(self, script_id: str) -> Script:
        """Step 5b: Merge selected videos into a single file."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        # Collect video paths
        video_paths = []
        for frame in script.frames:
            if not frame.selected_video_id:
                # Skip or fail? For now skip, but maybe warn?
                # Or try to find a default completed video?
                default_video = next((v for v in script.video_tasks if v.frame_id == frame.id and v.status == "completed"), None)
                if default_video and default_video.video_url:
                    video_paths.append(default_video.video_url)
                continue
                
            video = next((v for v in script.video_tasks if v.id == frame.selected_video_id), None)
            if video and video.video_url:
                video_paths.append(video.video_url)
                
        if not video_paths:
            raise ValueError("No videos selected to merge")
            
        # Create file list for ffmpeg
        list_path = os.path.join("output", f"merge_list_{script_id}.txt")
        abs_video_paths = []
        
        with open(list_path, "w") as f:
            for path in video_paths:
                # Resolve to absolute path
                if not path.startswith("http"):
                    abs_path = os.path.abspath(os.path.join("output", path))
                    if os.path.exists(abs_path):
                        f.write(f"file '{abs_path}'\n")
                        abs_video_paths.append(abs_path)
                        
        if not abs_video_paths:
             raise ValueError("No valid video files found")

        # Output path
        output_filename = f"merged_{script_id}_{int(time.time())}.mp4"
        output_path = os.path.join("output", "video", output_filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Run ffmpeg
        # ffmpeg -f concat -safe 0 -i list.txt -c copy output.mp4
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            
            # Update script
            script.merged_video_url = f"video/{output_filename}"
            self._save_data()
            
            # Cleanup list file
            if os.path.exists(list_path):
                os.remove(list_path)
                
            return script
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg merge failed: {e.stderr.decode()}")
            raise RuntimeError(f"Merge failed: {e.stderr.decode()}")

    def process_video_task(self, script_id: str, task_id: str):
        """Processes a video task."""
        script = self.get_script(script_id)
        if not script:
            print(f"Script {script_id} not found for task {task_id}")
            return
            
        task = next((t for t in script.video_tasks if t.id == task_id), None)
        
        if not task:
            print(f"Task {task_id} not found in script {script_id}")
            return

        try:
            # Update status to processing
            task.status = "processing"
            self._save_data()
            
            # Download image to temp file
            img_path = self._download_temp_image(task.image_url)
            
            # Generate video
            output_filename = f"video_{task_id}.mp4"
            output_path = os.path.join("output", "outputs", "videos", output_filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Handle Audio Logic
            # 1. Silent: audio_url=None, audio=False
            # 2. AI Sound: audio_url=None, audio=True
            # 3. Sound Driven: audio_url=URL (audio param ignored)
            
            final_audio_url = None
            final_generate_audio = False
            
            if task.audio_url:
                # Sound Driven Mode
                final_audio_url = task.audio_url
                final_generate_audio = False # API says audio param ignored if url present, but let's be explicit
            elif task.generate_audio:
                # AI Sound Mode
                final_audio_url = None
                final_generate_audio = True
            else:
                # Silent Mode
                final_audio_url = None
                final_generate_audio = False

            # Ensure img_url is passed correctly for OSS
            img_url = task.image_url
            
            video_path, _ = self.video_generator.model.generate(
                prompt=task.prompt,
                output_path=output_path,
                img_path=img_path,
                img_url=img_url,
                duration=task.duration,
                seed=task.seed,
                resolution=task.resolution,
                # Pass new params
                audio_url=final_audio_url,
                audio=final_generate_audio, # Pass as 'audio' to match Wan API expectation if needed, or keep generate_audio
                prompt_extend=task.prompt_extend,
                negative_prompt=task.negative_prompt,
                model=task.model,
                shot_type=task.shot_type,  # Pass shot_type for wan2.6-i2v (single/multi)
                # Legacy params mapped or ignored
                camera_motion=None, 
                subject_motion=None
            )
            
            task.video_url = os.path.relpath(output_path, "output")
            task.status = "completed"
            
        except Exception as e:
            print(f"Video generation failed: {e}")
            task.status = "failed"
            
        self._save_data()

    def generate_audio(self, script_id: str) -> Script:
        """Step 5: Generate audio (Dialogue & SFX)."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        logger.info(f"Generating audio for script {script.id}")
        
        for frame in script.frames:
            # Generate Dialogue
            if frame.dialogue:
                speaker = None
                if frame.character_ids:
                    speaker = next((c for c in script.characters if c.id == frame.character_ids[0]), None)
                
                if speaker:
                    self.audio_generator.generate_dialogue(frame, speaker)
            
            # Generate SFX (Text-to-Audio)
            if frame.action_description:
                self.audio_generator.generate_sfx(frame)
                
            # Generate SFX (Video-to-Audio) - if video exists
            if frame.video_url:
                self.audio_generator.generate_sfx_from_video(frame)
                
            # Generate BGM
            # Simple logic: generate BGM for every frame (or scene start)
            self.audio_generator.generate_bgm(frame)
                
        self._save_data()
        return script

    def generate_dialogue_line(self, script_id: str, frame_id: str, speed: float = 1.0, pitch: float = 1.0) -> Script:
        """Generates audio for a specific line with parameters."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        frame = next((f for f in script.frames if f.id == frame_id), None)
        if not frame:
            raise ValueError("Frame not found")
            
        if frame.dialogue:
            speaker = None
            if frame.character_ids:
                speaker = next((c for c in script.characters if c.id == frame.character_ids[0]), None)
            
            if speaker:
                self.audio_generator.generate_dialogue(frame, speaker, speed, pitch)
                
        self._save_data()
        return script

    def bind_voice(self, script_id: str, char_id: str, voice_id: str, voice_name: str) -> Script:
        """Binds a voice to a character."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        char = next((c for c in script.characters if c.id == char_id), None)
        if not char:
            raise ValueError("Character not found")
            
        char.voice_id = voice_id
        char.voice_name = voice_name
        self._save_data()
        return script

    def get_script(self, script_id: str) -> Optional[Script]:
        return self.scripts.get(script_id)

    def _select_variant_in_asset(self, image_asset: Any, variant_id: str) -> Any:
        """Helper to select a variant in an ImageAsset. Returns the selected variant if found."""
        if not image_asset or not image_asset.variants:
            return None
            
        for variant in image_asset.variants:
            if variant.id == variant_id:
                image_asset.selected_id = variant_id
                return variant
        return None

    def _delete_variant_in_asset(self, image_asset: Any, variant_id: str) -> bool:
        """Helper to delete a variant in an ImageAsset. Returns True if found and deleted."""
        if not image_asset or not image_asset.variants:
            return False
            
        initial_len = len(image_asset.variants)
        image_asset.variants = [v for v in image_asset.variants if v.id != variant_id]
        
        if len(image_asset.variants) < initial_len:
            # If we deleted the selected one, select the last one or None
            if image_asset.selected_id == variant_id:
                if image_asset.variants:
                    image_asset.selected_id = image_asset.variants[-1].id
                else:
                    image_asset.selected_id = None
            return True
        return False

    def select_asset_variant(self, script_id: str, asset_id: str, asset_type: str, variant_id: str, generation_type: str = None) -> Script:
        """Selects a specific variant for an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            if target_asset:
                # If generation_type is specified, only select from that specific asset
                if generation_type == "full_body":
                    variant = self._select_variant_in_asset(target_asset.full_body_asset, variant_id)
                    if variant:
                        target_asset.full_body_image_url = variant.url
                        target_asset.image_url = variant.url  # Legacy sync
                elif generation_type == "three_view":
                    variant = self._select_variant_in_asset(target_asset.three_view_asset, variant_id)
                    if variant:
                        target_asset.three_view_image_url = variant.url
                elif generation_type == "headshot":
                    variant = self._select_variant_in_asset(target_asset.headshot_asset, variant_id)
                    if variant:
                        target_asset.headshot_image_url = variant.url
                        target_asset.avatar_url = variant.url  # Sync avatar
                else:
                    # Legacy fallback: search all assets (for backward compatibility)
                    variant = self._select_variant_in_asset(target_asset.full_body_asset, variant_id)
                    if variant:
                        target_asset.full_body_image_url = variant.url
                        target_asset.image_url = variant.url
                    
                    if not variant:
                        variant = self._select_variant_in_asset(target_asset.three_view_asset, variant_id)
                        if variant:
                            target_asset.three_view_image_url = variant.url
                    
                    if not variant:
                        variant = self._select_variant_in_asset(target_asset.headshot_asset, variant_id)
                        if variant:
                            target_asset.headshot_image_url = variant.url
                            target_asset.avatar_url = variant.url
                        
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            if target_asset:
                variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                if variant:
                    target_asset.image_url = variant.url

        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            if target_asset:
                variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                if variant:
                    target_asset.image_url = variant.url

        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                # Check rendered_image_asset
                variant = self._select_variant_in_asset(target_asset.rendered_image_asset, variant_id)
                if variant:
                    target_asset.rendered_image_url = variant.url
                    target_asset.image_url = variant.url # Main image is rendered one
                
                # Also check image_asset (sketch)?
                if not variant:
                    variant = self._select_variant_in_asset(target_asset.image_asset, variant_id)
                    # If sketch, maybe don't update main image_url if rendered exists?
                    # For now, let's assume we only select rendered variants for frames usually.
        
        self._save_data()
        return script

    def delete_asset_variant(self, script_id: str, asset_id: str, asset_type: str, variant_id: str) -> Script:
        """Deletes a specific variant from an asset."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
            
        target_asset = None
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            if target_asset:
                if self._delete_variant_in_asset(target_asset.full_body_asset, variant_id):
                    # Sync legacy if needed
                    if target_asset.full_body_asset.selected_id:
                        selected = next((v for v in target_asset.full_body_asset.variants if v.id == target_asset.full_body_asset.selected_id), None)
                        target_asset.image_url = selected.url if selected else None
                    else:
                        target_asset.image_url = None
                
                elif self._delete_variant_in_asset(target_asset.three_view_asset, variant_id):
                    if target_asset.three_view_asset.selected_id:
                        selected = next((v for v in target_asset.three_view_asset.variants if v.id == target_asset.three_view_asset.selected_id), None)
                        target_asset.three_view_image_url = selected.url if selected else None
                    else:
                        target_asset.three_view_image_url = None

                elif self._delete_variant_in_asset(target_asset.headshot_asset, variant_id):
                    if target_asset.headshot_asset.selected_id:
                        selected = next((v for v in target_asset.headshot_asset.variants if v.id == target_asset.headshot_asset.selected_id), None)
                        target_asset.headshot_image_url = selected.url if selected else None
                    else:
                        target_asset.headshot_image_url = None

        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            if target_asset and self._delete_variant_in_asset(target_asset.image_asset, variant_id):
                if target_asset.image_asset.selected_id:
                    selected = next((v for v in target_asset.image_asset.variants if v.id == target_asset.image_asset.selected_id), None)
                    target_asset.image_url = selected.url if selected else None
                else:
                    target_asset.image_url = None

        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            if target_asset and self._delete_variant_in_asset(target_asset.image_asset, variant_id):
                if target_asset.image_asset.selected_id:
                    selected = next((v for v in target_asset.image_asset.variants if v.id == target_asset.image_asset.selected_id), None)
                    target_asset.image_url = selected.url if selected else None
                else:
                    target_asset.image_url = None

        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                if self._delete_variant_in_asset(target_asset.rendered_image_asset, variant_id):
                    if target_asset.rendered_image_asset.selected_id:
                        selected = next((v for v in target_asset.rendered_image_asset.variants if v.id == target_asset.rendered_image_asset.selected_id), None)
                        target_asset.rendered_image_url = selected.url if selected else None
                        target_asset.image_url = selected.url if selected else None
                    else:
                        target_asset.rendered_image_url = None
                        # Don't clear image_url if it might fall back to sketch? 
                        # For now, clear it if rendered is cleared.
                        target_asset.image_url = None

        self._save_data()
        return script

    def update_model_settings(self, script_id: str, t2i_model: str = None, i2i_model: str = None, i2v_model: str = None, character_aspect_ratio: str = None, scene_aspect_ratio: str = None, prop_aspect_ratio: str = None, storyboard_aspect_ratio: str = None) -> Script:
        """Updates the model settings for a script."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        
        if t2i_model:
            script.model_settings.t2i_model = t2i_model
        if i2i_model:
            script.model_settings.i2i_model = i2i_model
        if i2v_model:
            script.model_settings.i2v_model = i2v_model
        if character_aspect_ratio:
            script.model_settings.character_aspect_ratio = character_aspect_ratio
        if scene_aspect_ratio:
            script.model_settings.scene_aspect_ratio = scene_aspect_ratio
        if prop_aspect_ratio:
            script.model_settings.prop_aspect_ratio = prop_aspect_ratio
        if storyboard_aspect_ratio:
            script.model_settings.storyboard_aspect_ratio = storyboard_aspect_ratio
        
        self._save_data()
        return script

    def _set_variant_favorite(self, image_asset: Any, variant_id: str, is_favorited: bool) -> bool:
        """Helper to set favorite status of a variant. Returns True if found."""
        if not image_asset or not image_asset.variants:
            return False
        for v in image_asset.variants:
            if v.id == variant_id:
                v.is_favorited = is_favorited
                return True
        return False

    def toggle_variant_favorite(self, script_id: str, asset_id: str, asset_type: str, variant_id: str, is_favorited: bool, generation_type: str = None) -> Script:
        """Toggles the favorite status of a variant."""
        script = self.scripts.get(script_id)
        if not script:
            raise ValueError("Script not found")
        
        found = False
        if asset_type == "character":
            target_asset = next((c for c in script.characters if c.id == asset_id), None)
            if target_asset:
                if generation_type == "full_body":
                    found = self._set_variant_favorite(target_asset.full_body_asset, variant_id, is_favorited)
                elif generation_type == "three_view":
                    found = self._set_variant_favorite(target_asset.three_view_asset, variant_id, is_favorited)
                elif generation_type == "headshot":
                    found = self._set_variant_favorite(target_asset.headshot_asset, variant_id, is_favorited)
                else:
                    # Try all character assets
                    found = self._set_variant_favorite(target_asset.full_body_asset, variant_id, is_favorited) or \
                            self._set_variant_favorite(target_asset.three_view_asset, variant_id, is_favorited) or \
                            self._set_variant_favorite(target_asset.headshot_asset, variant_id, is_favorited)
        
        elif asset_type == "scene":
            target_asset = next((s for s in script.scenes if s.id == asset_id), None)
            if target_asset:
                found = self._set_variant_favorite(target_asset.image_asset, variant_id, is_favorited)
        
        elif asset_type == "prop":
            target_asset = next((p for p in script.props if p.id == asset_id), None)
            if target_asset:
                found = self._set_variant_favorite(target_asset.image_asset, variant_id, is_favorited)
        
        elif asset_type == "storyboard_frame":
            target_asset = next((f for f in script.frames if f.id == asset_id), None)
            if target_asset:
                found = self._set_variant_favorite(target_asset.rendered_image_asset, variant_id, is_favorited) or \
                        self._set_variant_favorite(target_asset.image_asset, variant_id, is_favorited)
        
        if not found:
            raise ValueError(f"Variant {variant_id} not found")
        
        self._save_data()
        return script
