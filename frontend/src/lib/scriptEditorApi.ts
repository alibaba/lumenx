import axios from 'axios';
import { API_URL } from '@/lib/apiUrl';
import type { L3Result } from '@/store/editorStore';

const API_BASE = API_URL;

export interface DocumentResponse {
  project_id: string;
  revision: number;
  updated_at: string | null;
  content: object;
}

export interface SnapshotResponse {
  project_id: string;
  snapshot_id: string;
  timestamp: string;
  created_at: string;
  revision: number;
  size_bytes: number;
}

export interface SaveDocumentOptions {
  expectedRevision: number;
  createSnapshot?: boolean;
}

export interface RestoreSnapshotOptions {
  expectedRevision: number;
}

export interface SyncDerivationOptions {
  expectedRevision: number;
  sourceDocumentRevision: number;
}

export interface DerivationSidecarResponse {
  status?: string;
  revision: number;
  updated_at: string;
  source_document_revision: number;
  data: Record<string, unknown>;
}

export interface DeriveGapsRequest {
  already_extracted?: { scenes: string[]; characters: string[] };
  gaps?: string[];
  raw_text_blocks?: string[];
}

export interface DeriveGapsOptions {
  signal?: AbortSignal;
}

export interface DeriveGapsClientResponse {
  results: L3Result[];
  cached: boolean;
}

export interface ConfirmShotBlockOptions {
  expectedRevision: number;
  sourceDocumentRevision: number;
}

export interface ShotBlockResponse {
  shot_id: string;
  status: string;
  revision: number;
  source_document_revision: number;
  [key: string]: unknown;
}

function assertDeriveGapsResponse(data: unknown): DeriveGapsClientResponse {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('Invalid derive_gaps response: expected object');
  }

  const raw = data as Record<string, unknown>;
  if (!Array.isArray(raw.entities)) {
    throw new Error('Invalid derive_gaps response: missing entities');
  }

  const results = raw.entities.map((item, index): L3Result => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error(`Invalid derive_gaps response: entity ${index} is not an object`);
    }
    const entity = item as Record<string, unknown>;
    if (
      entity.type !== 'character' &&
      entity.type !== 'prop' &&
      entity.type !== 'beat' &&
      entity.type !== 'location'
    ) {
      throw new Error(`Invalid derive_gaps response: entity ${index} has invalid type`);
    }
    if (typeof entity.name !== 'string' || entity.name.trim() === '') {
      throw new Error(`Invalid derive_gaps response: entity ${index} has invalid name`);
    }
    if (typeof entity.confidence !== 'number' || !Number.isFinite(entity.confidence) || entity.confidence < 0 || entity.confidence > 1) {
      throw new Error(`Invalid derive_gaps response: entity ${index} has invalid confidence`);
    }
    if (entity.description !== undefined && entity.description !== null && typeof entity.description !== 'string') {
      throw new Error(`Invalid derive_gaps response: entity ${index} has invalid description`);
    }
    if (
      entity.scene_index !== undefined &&
      entity.scene_index !== null &&
      (!Number.isSafeInteger(entity.scene_index) || (entity.scene_index as number) < 0)
    ) {
      throw new Error(`Invalid derive_gaps response: entity ${index} has invalid scene_index`);
    }

    return {
      type: entity.type,
      name: entity.name.trim(),
      ...(typeof entity.description === 'string' ? { description: entity.description } : {}),
      confidence: entity.confidence,
      ...(entity.scene_index !== undefined && entity.scene_index !== null ? { sceneIndex: entity.scene_index as number } : {}),
    };
  });

  return {
    results,
    cached: raw.cached === true,
  };
}

export const scriptEditorApi = {
  /** 保存文档 */
  saveDocument: async (projectId: string, content: object, options: SaveDocumentOptions): Promise<DocumentResponse> => {
    const res = await axios.post(`${API_BASE}/projects/${projectId}/document`, {
      content,
      expected_revision: options.expectedRevision,
      create_snapshot: options.createSnapshot ?? false,
    });
    return res.data;
  },

  /** 加载文档 */
  loadDocument: async (projectId: string, options?: { signal?: AbortSignal }): Promise<DocumentResponse> => {
    const res = await axios.get(`${API_BASE}/projects/${projectId}/document`, { signal: options?.signal });
    return res.data;
  },

  /** 列出快照 */
  listSnapshots: async (projectId: string): Promise<SnapshotResponse[]> => {
    const res = await axios.get(`${API_BASE}/projects/${projectId}/document/snapshots`);
    return res.data;
  },

  /** 创建快照 */
  createSnapshot: async (projectId: string): Promise<SnapshotResponse> => {
    const res = await axios.post(`${API_BASE}/projects/${projectId}/document/snapshots`);
    return res.data;
  },

  /** 恢复快照 */
  restoreSnapshot: async (projectId: string, timestamp: string, options: RestoreSnapshotOptions): Promise<DocumentResponse> => {
    const res = await axios.post(
      `${API_BASE}/projects/${projectId}/document/snapshots/${timestamp}/restore`,
      { expected_revision: options.expectedRevision }
    );
    return res.data;
  },

  /** 导入文档（FDX/Fountain/TXT → Tiptap JSON） */
  importDocument: async (projectId: string, file: File): Promise<any> => {
    const buffer = await file.arrayBuffer();
    const base64 = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), '')
    );

    const ext = file.name.split('.').pop()?.toLowerCase() || 'txt';
    const fileType = ext === 'fdx' ? 'fdx' : ext === 'fountain' ? 'fountain' : 'txt';

    const res = await axios.post(`${API_BASE}/projects/${projectId}/document/import`, {
      filename: file.name,
      content: base64,
      file_type: fileType,
    });
    return res.data;
  },

  /** 导出文档（Tiptap JSON → PDF/DOCX，返回 Blob） */
  exportDocument: async (projectId: string, content: any, format: string): Promise<Blob> => {
    const res = await axios.post(
      `${API_BASE}/projects/${projectId}/document/export`,
      { content, format, options: {} },
      { responseType: 'blob' }
    );
    return res.data;
  },

  /** 同步派生数据到后端 */
  syncDerivation: async (
    projectId: string,
    data: Record<string, unknown>,
    options: SyncDerivationOptions
  ): Promise<DerivationSidecarResponse> => {
    const res = await axios.post(`${API_BASE}/projects/${projectId}/sync_derivation`, {
      ...data,
      expected_revision: options.expectedRevision,
      source_document_revision: options.sourceDocumentRevision,
    });
    return res.data;
  },

  /** L3 LLM 增量补全请求 */
  deriveGaps: async (
    projectId: string,
    params: DeriveGapsRequest,
    options?: DeriveGapsOptions
  ): Promise<DeriveGapsClientResponse> => {
    const res = await axios.post(`${API_BASE}/projects/${projectId}/derive_gaps`, params, { signal: options?.signal });
    return assertDeriveGapsResponse(res.data);
  },

  /** 确认 ShotBlock */
  confirmShotBlock: async (
    projectId: string,
    shotId: string,
    data: Record<string, unknown>,
    options: ConfirmShotBlockOptions
  ): Promise<ShotBlockResponse> => {
    const res = await axios.post(
      `${API_BASE}/projects/${projectId}/shot_blocks/${shotId}/confirm`,
      {
        ...data,
        expected_revision: options.expectedRevision,
        source_document_revision: options.sourceDocumentRevision,
      }
    );
    return res.data;
  },
};
