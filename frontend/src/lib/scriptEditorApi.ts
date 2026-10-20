import axios from 'axios';
import type { L3Result } from '@/store/editorStore';

// Reuse the same API URL detection logic as the main api.ts
const BACKEND_PORT = process.env.NEXT_PUBLIC_BACKEND_PORT || '17177';

const getApiUrl = (): string => {
  const override = process.env.NEXT_PUBLIC_API_URL;
  if (override && override.trim()) {
    return override.trim().replace(/\/+$/, '');
  }

  if (typeof window !== 'undefined') {
    const { protocol, hostname, port } = window.location;

    if (process.env.NODE_ENV === 'development') {
      return `${protocol}//${hostname}:${BACKEND_PORT}`;
    }

    return `${protocol}//${hostname}${port ? ':' + port : ''}`;
  }

  return `http://localhost:${BACKEND_PORT}`;
};

const API_BASE = getApiUrl();

export interface DocumentResponse {
  project_id: string;
  content: object;
  updated_at: string;
}

export interface SnapshotResponse {
  project_id: string;
  timestamp: string;
  created_at: string;
}

export const scriptEditorApi = {
  /** 保存文档 */
  saveDocument: async (projectId: string, content: object, createSnapshot = false): Promise<DocumentResponse> => {
    const res = await axios.post(`${API_BASE}/projects/${projectId}/document`, {
      content,
      create_snapshot: createSnapshot,
    });
    return res.data;
  },

  /** 加载文档 */
  loadDocument: async (projectId: string): Promise<DocumentResponse> => {
    const res = await axios.get(`${API_BASE}/projects/${projectId}/document`);
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
  restoreSnapshot: async (projectId: string, timestamp: string): Promise<DocumentResponse> => {
    const res = await axios.post(
      `${API_BASE}/projects/${projectId}/document/snapshots/${timestamp}/restore`
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
  syncDerivation: async (projectId: string, data: any): Promise<void> => {
    await axios.post(`${API_BASE}/projects/${projectId}/sync_derivation`, data);
  },

  /** L3 LLM 增量补全请求 */
  deriveGaps: async (
    projectId: string,
    params: {
      already_extracted?: { scenes: string[]; characters: string[] };
      gaps?: string[]; // e.g. ['props', 'beats', 'locations']
    }
  ): Promise<{ results: L3Result[]; task_id?: string }> => {
    const res = await axios.post(`${API_BASE}/projects/${projectId}/derive_gaps`, params);
    return res.data;
  },

  /** 确认 ShotBlock */
  confirmShotBlock: async (projectId: string, shotId: string, data: any): Promise<any> => {
    const res = await axios.post(
      `${API_BASE}/projects/${projectId}/shot_blocks/${shotId}/confirm`,
      data
    );
    return res.data;
  },
};
