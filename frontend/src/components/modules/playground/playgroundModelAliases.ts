const PLAYGROUND_MODEL_ALIASES: Record<string, string> = {
  'seedance-2.0-t2v': 'seedance-2.0-t2v',
  'seedance/seedance-2.0-video#t2v': 'seedance-2.0-t2v',
  'seedance-2.0-i2v': 'seedance-2.0-i2v',
  'seedance/seedance-2.0-video#i2v': 'seedance-2.0-i2v',
  'seedance-2.0-r2v': 'seedance-2.0-r2v',
  'seedance/seedance-2.0-video#r2v': 'seedance-2.0-r2v',

  'pixverse/pixverse-v6-video': 'pixverse/pixverse-v6-video',
  'pixverse-c1-i2v': 'pixverse-c1-i2v',
  'pixverse-c1-r2v': 'pixverse-c1-r2v',
  'pixverse-v5.6-r2v': 'pixverse-v5.6-r2v',
  'pixverse/pixverse/pixverse-v6-video#i2v': 'pixverse/pixverse-v6-video',
  'pixverse/pixverse-c1-video#i2v': 'pixverse-c1-i2v',
  'pixverse/pixverse-c1-video#r2v': 'pixverse-c1-r2v',
  'pixverse/pixverse-v5.6-r2v#r2v': 'pixverse-v5.6-r2v',
  'kling-v3-i2v': 'kling-v3-i2v',
  'kling-v3-r2v': 'kling-v3-r2v',
  'kling/kling-v3-video#i2v': 'kling-v3-i2v',
  'kling/kling-v3-video#r2v': 'kling-v3-r2v',
  'viduq3-pro-i2v': 'viduq3-pro-i2v',
  'viduq3-turbo-i2v': 'viduq3-turbo-i2v',
  'viduq3-pro-r2v': 'viduq3-pro-r2v',
  'viduq3-turbo-r2v': 'viduq3-turbo-r2v',
  'vidu/viduq3-pro-video#i2v': 'viduq3-pro-i2v',
  'vidu/viduq3-turbo-video#i2v': 'viduq3-turbo-i2v',
  'vidu/viduq3-pro-video#r2v': 'viduq3-pro-r2v',
  'vidu/viduq3-turbo-video#r2v': 'viduq3-turbo-r2v',
  'wan2.7-t2v': 'wan2.7-t2v',
  'wan2.7-i2v': 'wan2.7-i2v',
  'wan2.7-r2v': 'wan2.7-r2v',
  'wan2.7-videoedit': 'wan2.7-videoedit',
  'wan/wan2.7-video#t2v': 'wan2.7-t2v',
  'wan/wan2.7-video#i2v': 'wan2.7-i2v',
  'wan/wan2.7-video#r2v': 'wan2.7-r2v',
  'wan/wan2.7-video#v2v': 'wan2.7-videoedit',
  'wan/wan2.7-video#videoedit': 'wan2.7-videoedit',
  'happyhorse-1.1-t2v': 'happyhorse-1.1-t2v',
  'happyhorse-1.1-i2v': 'happyhorse-1.1-i2v',
  'happyhorse-1.1-r2v': 'happyhorse-1.1-r2v',
  'happyhorse-1.0-video-edit': 'happyhorse-1.0-video-edit',
  'happyhorse/happyhorse-1.1-t2v#t2v': 'happyhorse-1.1-t2v',
  'happyhorse/happyhorse-1.1-t2v#i2v': 'happyhorse-1.1-t2v',
  'happyhorse/happyhorse-1.1-video#i2v': 'happyhorse-1.1-i2v',
  'happyhorse/happyhorse-1.1-video#r2v': 'happyhorse-1.1-r2v',
  'happyhorse/happyhorse-1.0-video-edit#v2v': 'happyhorse-1.0-video-edit',
  'happyhorse/happyhorse-1.0-video-edit#i2v': 'happyhorse-1.0-video-edit',
};

const WAN27_IMAGE_ALIASES: Record<string, string> = {
  'wan2.7-image': 'wan2.7-image',
  'wan2.7-image-pro': 'wan2.7-image-pro',
  'wan/wan2.7-image#image': 'wan2.7-image',
  'wan/wan2.7-image-pro#image': 'wan2.7-image-pro',
};

export function resolveWan27PlaygroundImageModelId(modelId: string): string | null {
  return Object.prototype.hasOwnProperty.call(WAN27_IMAGE_ALIASES, modelId) ? WAN27_IMAGE_ALIASES[modelId] : null;
}

const QWEN20_IMAGE_ALIASES: Record<string, string> = {
  'qwen-image-2.0': 'qwen-image-2.0',
  'qwen-image-2.0-pro': 'qwen-image-2.0-pro',
  'qwen/qwen-image-2.0#image': 'qwen-image-2.0',
  'qwen/qwen-image-2.0-pro#image': 'qwen-image-2.0-pro',
};

export function resolveQwen20PlaygroundImageModelId(modelId: string): string | null {
  return Object.prototype.hasOwnProperty.call(QWEN20_IMAGE_ALIASES, modelId) ? QWEN20_IMAGE_ALIASES[modelId] : null;
}

const WORKSPACE_GATEWAY_PLAYGROUND_ALLOWLIST = new Set([
  'pixverse/pixverse-v6-video',
  'pixverse-c1-i2v',
  'pixverse-c1-r2v',
  'pixverse-v5.6-r2v',
  'kling-v3-i2v',
  'kling-v3-r2v',
  'viduq3-pro-i2v',
  'viduq3-turbo-i2v',
  'viduq3-pro-r2v',
  'viduq3-turbo-r2v',
  'happyhorse-1.1-t2v',
  'happyhorse-1.1-i2v',
  'happyhorse-1.1-r2v',
  'happyhorse-1.0-video-edit',
]);

export function resolvePlaygroundCatalogModelId(modelId: string): string {
  return PLAYGROUND_MODEL_ALIASES[modelId] ?? resolveWan27PlaygroundImageModelId(modelId) ?? resolveQwen20PlaygroundImageModelId(modelId) ?? modelId;
}

export function resolveMigratedPlaygroundVideoModelId(modelId: string): string | null {
  return PLAYGROUND_MODEL_ALIASES[modelId] ?? null;
}

export function allowsPlaygroundWorkspaceGateway(modelId: string): boolean {
  return WORKSPACE_GATEWAY_PLAYGROUND_ALLOWLIST.has(resolvePlaygroundCatalogModelId(modelId));
}
