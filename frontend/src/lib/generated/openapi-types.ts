/* eslint-disable */
// This file is generated from FastAPI OpenAPI. Do not edit by hand.
// Run `npm -C frontend run generate:api-types` after backend schema changes.

export interface AddCharacterRequest {
  name: string;
  description: string;
}

export interface AddEpisodeRequest {
  script_id: string;
  episode_number?: number | null;
}

export interface AddFrameRequest {
  scene_id?: string | null;
  action_description?: string;
  camera_angle?: string;
  insert_at?: number | null;
}

export interface AddSceneRequest {
  name: string;
  description: string;
}

export interface AnalyzeBeatStoryboardRequest {
  beat_id: string;
}

export interface AnalyzeStyleRequest {
  script_text: string;
}

export interface AnalyzeToStoryboardRequest {
  text: string;
}

export interface ArtDirection {
  selected_style_id: string;
  style_config: Record<string, unknown>;
  custom_styles?: Array<Record<string, unknown>>;
  ai_recommendations?: Array<Record<string, unknown>>;
}

export interface AssetUnit {
  selected_image_id?: string | null;
  image_variants?: Array<ImageVariant>;
  selected_video_id?: string | null;
  video_variants?: Array<VideoVariant>;
  image_prompt?: string | null;
  video_prompt?: string | null;
  image_updated_at?: number;
  video_updated_at?: number;
}

export interface BindVoiceRequest {
  voice_id: string;
  voice_name: string;
}

export interface Body_import_file_preview_series_import_preview_post {
  file: string;
}

export interface Body_upload_asset_projects__script_id__assets__asset_type___asset_id__upload_post {
  file: string;
}

export interface Body_upload_file_upload_post {
  file: string;
}

export interface Body_upload_frame_image_projects__script_id__frames__frame_id__upload_image_post {
  file: string;
}

export interface Character {
  id: string;
  name: string;
  aliases?: Array<string>;
  description: string;
  age?: string | null;
  gender?: string | null;
  clothing?: string | null;
  visual_weight?: number;
  full_body?: AssetUnit | null;
  three_views?: AssetUnit | null;
  head_shot?: AssetUnit | null;
  full_body_image_url?: string | null;
  full_body_prompt?: string | null;
  full_body_asset?: ImageAsset | null;
  three_view_image_url?: string | null;
  three_view_prompt?: string | null;
  three_view_asset?: ImageAsset | null;
  headshot_image_url?: string | null;
  headshot_prompt?: string | null;
  headshot_asset?: ImageAsset | null;
  video_assets?: Array<VideoTask>;
  video_prompt?: string | null;
  image_url?: string | null;
  avatar_url?: string | null;
  is_consistent?: boolean;
  full_body_updated_at?: number;
  three_view_updated_at?: number;
  headshot_updated_at?: number;
  base_character_id?: string | null;
  voice_id?: string | null;
  voice_name?: string | null;
  voice_speed?: number;
  voice_pitch?: number;
  voice_volume?: number;
  locked?: boolean;
  status?: GenerationStatus;
}

export interface CharacterPresenceEntry {
  character_id: string;
  character_name: string;
  scene_beat_ids?: Array<string>;
  scene_titles?: Array<string>;
  mention_count?: number;
  highlights?: Array<string>;
}

export interface CharacterRelationshipEdge {
  pair_id: string;
  source_character_id: string;
  source_character_name: string;
  target_character_id: string;
  target_character_name: string;
  co_scene_count?: number;
  shared_scene_beat_ids?: Array<string>;
  shared_scene_titles?: Array<string>;
  relationship_hint?: string;
}

export interface ComposeFrameCropsRequest {
  manifest_path?: string | null;
  output_path?: string | null;
  verify?: boolean;
}

export interface ConfirmImportRequest {
  title: string;
  description?: string;
  import_id?: string;
  text?: string | null;
  episodes: Array<Record<string, unknown>>;
}

export interface CopyFrameRequest {
  frame_id: string;
  insert_at?: number | null;
}

export interface CreateProjectRequest {
  title: string;
  text: string;
}

export interface CreatePropRequest {
  name: string;
  description?: string;
}

export interface CreateSeriesRequest {
  title: string;
  description?: string;
}

export interface CreateVideoTaskRequest {
  image_url: string;
  prompt: string;
  frame_id?: string | null;
  duration?: number;
  seed?: number | null;
  resolution?: string;
  generate_audio?: boolean;
  audio_url?: string | null;
  prompt_extend?: boolean;
  negative_prompt?: string | null;
  batch_size?: number;
  model?: string;
  aspect_ratio?: string | null;
  watermark?: boolean;
  camera_fixed?: boolean | null;
  reference_audio_url?: string | null;
  seedance_reference_mode?: string | null;
  seedance_workflow?: string | null;
  seedance_extend_mode?: string | null;
  seedance_edit_mode?: string | null;
  shot_type?: string;
  generation_mode?: string;
  reference_video_urls?: Array<string>;
  mode?: string | null;
  sound?: string | null;
  cfg_scale?: number | null;
  vidu_audio?: boolean | null;
  movement_amplitude?: string | null;
}

export interface DeleteResponse {
  status: string;
}

export interface DeleteVariantRequest {
  asset_id: string;
  asset_type: string;
  variant_id: string;
}

export interface EnvConfig {
  KLING_PROVIDER_MODE?: ProviderBackend;
  VIDU_PROVIDER_MODE?: ProviderBackend;
  PIXVERSE_PROVIDER_MODE?: ProviderBackend;
  IMAGE_PROVIDER?: string | null;
  IMAGE_EDIT_PROVIDER?: string | null;
  TTS_PROVIDER?: string | null;
  LLM_PROVIDER?: string | null;
  OPENAI_API_KEY?: string | null;
  OPENAI_BASE_URL?: string | null;
  OPENAI_MODEL?: string | null;
  OPENAI_IMAGE_API_KEY?: string | null;
  OPENAI_IMAGE_EDIT_API_KEY?: string | null;
  OPENAI_IMAGE_BASE_URL?: string | null;
  OPENAI_IMAGE_EDIT_BASE_URL?: string | null;
  OPENAI_IMAGE_MODEL?: string | null;
  OPENAI_IMAGE_EDIT_MODEL?: string | null;
  OPENAI_TTS_API_KEY?: string | null;
  OPENAI_TTS_BASE_URL?: string | null;
  OPENAI_TTS_MODEL?: string | null;
  OPENAI_MULTIMODAL_API_KEY?: string | null;
  OPENAI_MULTIMODAL_BASE_URL?: string | null;
  OPENAI_MULTIMODAL_MODEL?: string | null;
  ARK_API_KEY?: string | null;
  DASHSCOPE_API_KEY?: string | null;
  OBJECT_STORAGE_PROVIDER?: string | null;
  OBJECT_STORAGE_BUCKET_NAME?: string | null;
  OBJECT_STORAGE_ENDPOINT?: string | null;
  OBJECT_STORAGE_REGION?: string | null;
  OBJECT_STORAGE_BASE_PATH?: string | null;
  TOS_ACCESS_KEY_ID?: string | null;
  TOS_SECRET_ACCESS_KEY?: string | null;
  ALIBABA_CLOUD_ACCESS_KEY_ID?: string | null;
  ALIBABA_CLOUD_ACCESS_KEY_SECRET?: string | null;
  OSS_BUCKET_NAME?: string | null;
  OSS_ENDPOINT?: string | null;
  OSS_BASE_PATH?: string | null;
  KLING_ACCESS_KEY?: string | null;
  KLING_SECRET_KEY?: string | null;
  VIDU_API_KEY?: string | null;
  endpoint_overrides?: Record<string, string>;
}

export interface ExportRequest {
  resolution?: string;
  format?: string;
  subtitles?: string;
}

export interface ExportResponse {
  url: string;
  subtitle_url?: string | null;
  subtitle_format?: string | null;
  format?: string | null;
  resolution?: string | null;
}

export interface ExtractLastFrameRequest {
  video_task_id: string;
}

export interface FavoriteVariantRequest {
  asset_id: string;
  asset_type: string;
  variant_id: string;
  generation_type?: string | null;
  is_favorited: boolean;
}

export interface GenerateAssetRequest {
  asset_id: string;
  asset_type: string;
  style_preset?: string;
  reference_image_url?: string | null;
  style_prompt?: string | null;
  generation_type?: string;
  prompt?: string | null;
  apply_style?: boolean;
  negative_prompt?: string | null;
  batch_size?: number;
  model_name?: string | null;
}

export interface GenerateAssetVideoRequest {
  prompt?: string | null;
  duration?: number;
  aspect_ratio?: string | null;
}

export interface GenerateLineAudioRequest {
  speed?: number;
  pitch?: number;
  volume?: number;
}

export interface GenerateMotionRefRequest {
  asset_id: string;
  asset_type: string;
  prompt?: string | null;
  audio_url?: string | null;
  duration?: number;
  batch_size?: number;
}

export type GenerationStatus = "pending" | "processing" | "completed" | "failed";

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface ImageAsset {
  selected_id?: string | null;
  variants?: Array<ImageVariant>;
}

export interface ImageVariant {
  id: string;
  url: string;
  created_at?: number;
  prompt_used?: string | null;
  is_favorited?: boolean;
  is_uploaded_source?: boolean;
  upload_type?: string | null;
}

export interface ImportAssetsRequest {
  source_series_id: string;
  asset_ids: Array<string>;
}

export interface ModelSettings {
  t2i_model?: string;
  i2i_model?: string;
  i2v_model?: string;
  character_aspect_ratio?: string;
  scene_aspect_ratio?: string;
  prop_aspect_ratio?: string;
  storyboard_aspect_ratio?: string;
}

export interface PolishR2VPromptRequest {
  draft_prompt: string;
  slots: Array<RefSlot>;
  feedback?: string;
  script_id?: string;
}

export interface PolishVideoPromptRequest {
  draft_prompt: string;
  feedback?: string;
  script_id?: string;
}

export interface PromptConfig {
  storyboard_polish?: string;
  video_polish?: string;
  r2v_polish?: string;
}

export interface PromptConfigResponse {
  prompt_config: PromptConfig;
  defaults?: PromptConfig | null;
}

export interface Prop {
  id: string;
  name: string;
  description: string;
  video_url?: string | null;
  audio_url?: string | null;
  sfx_url?: string | null;
  bgm_url?: string | null;
  image_url?: string | null;
  image_asset?: ImageAsset | null;
  image_prompt?: string | null;
  video_assets?: Array<VideoTask>;
  video_prompt?: string | null;
  locked?: boolean;
  status?: GenerationStatus;
}

export type ProviderBackend = "dashscope" | "vendor";

export interface RefinePromptRequest {
  frame_id: string;
  raw_prompt: string;
  assets?: Array<unknown>;
  feedback?: string;
}

export interface RefSlot {
  description: string;
}

export interface RenderFrameRequest {
  frame_id: string;
  composition_data?: Record<string, unknown> | null;
  prompt: string;
  batch_size?: number;
}

export interface ReorderFramesRequest {
  frame_ids: Array<string>;
}

export interface ReparseProjectRequest {
  text: string;
}

export interface SaveArtDirectionRequest {
  selected_style_id: string;
  style_config: Record<string, unknown>;
  custom_styles?: Array<Record<string, unknown>>;
  ai_recommendations?: Array<Record<string, unknown>>;
}

export interface Scene {
  id: string;
  name: string;
  description: string;
  visual_weight?: number;
  time_of_day?: string | null;
  lighting_mood?: string | null;
  image_url?: string | null;
  image_asset?: ImageAsset | null;
  image_prompt?: string | null;
  video_assets?: Array<VideoTask>;
  video_prompt?: string | null;
  locked?: boolean;
  status?: GenerationStatus;
}

export interface Script {
  id: string;
  title: string;
  original_text: string;
  fixture_slug?: string | null;
  fixture_name?: string | null;
  fixture_project_type?: string | null;
  characters?: Array<Character>;
  scenes?: Array<Scene>;
  props?: Array<Prop>;
  frames?: Array<StoryboardFrame>;
  video_tasks?: Array<VideoTask>;
  style_preset?: string;
  style_prompt?: string | null;
  art_direction?: ArtDirection | null;
  model_settings?: ModelSettings;
  prompt_config?: PromptConfig;
  story_analysis?: StoryAnalysis;
  generation_metadata?: Record<string, unknown>;
  merged_video_url?: string | null;
  series_id?: string | null;
  episode_number?: number | null;
  created_at: number;
  updated_at: number;
}

export interface SelectVariantRequest {
  asset_id: string;
  asset_type: string;
  variant_id: string;
  generation_type?: string;
}

export interface SelectVideoRequest {
  video_id: string;
}

export interface Series {
  id: string;
  title: string;
  description?: string;
  characters?: Array<Character>;
  scenes?: Array<Scene>;
  props?: Array<Prop>;
  art_direction?: ArtDirection | null;
  prompt_config?: PromptConfig;
  model_settings?: ModelSettings;
  episode_ids?: Array<string>;
  created_at: number;
  updated_at: number;
}

export interface SeriesAssetsResponse {
  characters?: Array<Character>;
  scenes?: Array<Scene>;
  props?: Array<Prop>;
}

export interface SeriesDetailResponse {
  id: string;
  title: string;
  description?: string;
  characters?: Array<Character>;
  scenes?: Array<Scene>;
  props?: Array<Prop>;
  art_direction?: ArtDirection | null;
  prompt_config?: PromptConfig;
  model_settings?: ModelSettings;
  episode_ids?: Array<string>;
  created_at: number;
  updated_at: number;
  episodes?: Array<SeriesEpisodeSummary>;
}

export interface SeriesEpisodeSummary {
  id: string;
  title: string;
  episode_number?: number | null;
  created_at: number;
  updated_at: number;
}

export interface StoryAnalysis {
  summary?: string;
  plot_points?: Array<string>;
  scene_beats?: Array<StoryBeat>;
  character_presence?: Array<CharacterPresenceEntry>;
  character_relationships?: Array<CharacterRelationshipEdge>;
}

export interface StoryBeat {
  id: string;
  order: number;
  title: string;
  chapter_order?: number | null;
  chapter_title?: string | null;
  summary?: string;
  action_summary?: string;
  dialogue_excerpt?: string;
  storyboard_goal?: string;
  scene_id?: string | null;
  scene_name?: string | null;
  location_hint?: string | null;
  time_hint?: string | null;
  character_ids?: Array<string>;
  character_names?: Array<string>;
  prop_ids?: Array<string>;
  prop_names?: Array<string>;
  source_excerpt?: string | null;
  storyboard_focus?: string | null;
  quality_flags?: Array<string>;
}

export interface StoryboardFrame {
  id: string;
  scene_id: string;
  story_beat_id?: string | null;
  story_beat_title?: string | null;
  story_beat_order?: number | null;
  chapter_order?: number | null;
  chapter_title?: string | null;
  character_ids?: Array<string>;
  prop_ids?: Array<string>;
  action_description?: string;
  facial_expression?: string | null;
  dialogue?: string | null;
  speaker?: string | null;
  visual_atmosphere?: string | null;
  character_acting?: string | null;
  key_action_physics?: string | null;
  shot_size?: string | null;
  camera_angle?: string;
  camera_movement?: string | null;
  composition?: string | null;
  atmosphere?: string | null;
  composition_data?: Record<string, unknown> | null;
  image_prompt?: string | null;
  image_prompt_cn?: string | null;
  image_prompt_en?: string | null;
  image_url?: string | null;
  image_asset?: ImageAsset | null;
  rendered_image_url?: string | null;
  rendered_image_asset?: ImageAsset | null;
  video_prompt?: string | null;
  video_url?: string | null;
  audio_url?: string | null;
  audio_error?: string | null;
  sfx_url?: string | null;
  bgm_url?: string | null;
  generation_source?: string | null;
  generation_degraded?: boolean;
  generation_reason?: string | null;
  selected_video_id?: string | null;
  locked?: boolean;
  status?: GenerationStatus;
  updated_at?: number;
}

export interface ToggleFrameLockRequest {
  frame_id: string;
}

export interface ToggleLockRequest {
  asset_id: string;
  asset_type: string;
}

export interface UpdateAssetAttributesRequest {
  asset_id: string;
  asset_type: string;
  attributes: Record<string, unknown>;
}

export interface UpdateAssetDescriptionRequest {
  asset_id: string;
  asset_type: string;
  description: string;
}

export interface UpdateAssetImageRequest {
  asset_id: string;
  asset_type: string;
  image_url: string;
}

export interface UpdateFrameRequest {
  frame_id: string;
  image_prompt?: string | null;
  action_description?: string | null;
  dialogue?: string | null;
  camera_angle?: string | null;
  scene_id?: string | null;
  character_ids?: Array<string> | null;
}

export interface UpdateModelSettingsRequest {
  t2i_model?: string | null;
  i2i_model?: string | null;
  i2v_model?: string | null;
  character_aspect_ratio?: string | null;
  scene_aspect_ratio?: string | null;
  prop_aspect_ratio?: string | null;
  storyboard_aspect_ratio?: string | null;
}

export interface UpdatePromptConfigRequest {
  storyboard_polish?: string;
  video_polish?: string;
  r2v_polish?: string;
}

export interface UpdateSeriesRequest {
  title?: string | null;
  description?: string | null;
}

export interface UpdateStoryBeatRequest {
  action_summary?: string | null;
  dialogue_excerpt?: string | null;
  storyboard_goal?: string | null;
}

export interface UpdateStyleRequest {
  style_preset: string;
  style_prompt?: string | null;
}

export interface UpdateVoiceParamsRequest {
  speed?: number;
  pitch?: number;
  volume?: number;
}

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
}

export interface VideoTask {
  id: string;
  project_id: string;
  frame_id?: string | null;
  asset_id?: string | null;
  image_url: string;
  prompt: string;
  status?: string;
  video_url?: string | null;
  duration?: number;
  seed?: number | null;
  resolution?: string;
  generate_audio?: boolean;
  audio_url?: string | null;
  prompt_extend?: boolean;
  negative_prompt?: string | null;
  model?: string;
  aspect_ratio?: string | null;
  watermark?: boolean;
  camera_fixed?: boolean | null;
  reference_audio_url?: string | null;
  seedance_reference_mode?: string | null;
  seedance_workflow?: string | null;
  seedance_extend_mode?: string | null;
  seedance_edit_mode?: string | null;
  shot_type?: string;
  generation_mode?: string;
  reference_video_urls?: Array<string>;
  mode?: string | null;
  sound?: string | null;
  cfg_scale?: number | null;
  vidu_audio?: boolean | null;
  movement_amplitude?: string | null;
  created_at?: number;
}

export interface VideoVariant {
  id: string;
  url: string;
  created_at?: number;
  prompt_used?: string | null;
  audio_url?: string | null;
  source_image_id?: string | null;
  is_favorited?: boolean;
}
