import { create } from 'zustand';

// L3 补全结果类型
export interface L3Result {
  type: 'character' | 'prop' | 'beat' | 'location';
  name: string;
  description?: string;
  confidence: number; // 0-1
  sceneIndex?: number;
}

export type L3Status = 'idle' | 'loading' | 'success' | 'error';

// 类型定义
export interface DerivedScene {
  id: string;
  number: number | null;
  intExt: string | null;
  location: string | null;
  timeOfDay: string | null;
  title: string; // 场景标题文本
}

export interface DerivedCharacter {
  id: string;
  name: string;
  occurrences: number; // 出场次数
  firstAppearance: number; // 首次出场场景编号
}

export type ScriptFormat = 'hollywood' | 'chinese_film' | 'chinese_short' | 'japanese_anime';
export type TextRendering = 'latin' | 'cjk_zh' | 'cjk_ja';
export type ViewMode = 'edit' | 'storyboard' | 'read' | 'focus';
export type EditorMode = 'full' | 'embedded' | 'focus';

interface EditorState {
  // 核心状态
  projectId: string | null;
  isDirty: boolean;
  lastSavedAt: Date | null;
  isLoading: boolean;

  // 格式状态
  currentFormat: ScriptFormat;
  currentRendering: TextRendering;

  // 视图状态
  viewMode: ViewMode;
  editorMode: EditorMode;
  leftSidebarCollapsed: boolean;
  rightSidebarCollapsed: boolean;
  activeRightPanel: 'characters' | 'locations' | 'props' | 'shots' | 'pipeline' | 'notes' | 'ai';
  rightPanelLocked: boolean;

  // 派生数据
  derivedScenes: DerivedScene[];
  derivedCharacters: DerivedCharacter[];
  estimatedDuration: number; // 秒
  wordCount: number;
  confidenceScore: number; // 0-1

  // L3 LLM 补全状态
  l3Status: L3Status;
  l3Results: L3Result[] | null;
  l3LastFetchTime: number | null;

  // Actions
  setProjectId: (id: string | null) => void;
  setDirty: (dirty: boolean) => void;
  setLastSavedAt: (date: Date | null) => void;
  setLoading: (loading: boolean) => void;
  setFormat: (format: ScriptFormat) => void;
  setRendering: (rendering: TextRendering) => void;
  setViewMode: (mode: ViewMode) => void;
  setEditorMode: (mode: EditorMode) => void;
  toggleLeftSidebar: () => void;
  toggleRightSidebar: () => void;
  setActiveRightPanel: (panel: EditorState['activeRightPanel']) => void;
  setRightPanelLocked: (locked: boolean) => void;

  // 派生数据更新
  updateDerivation: (data: {
    scenes?: DerivedScene[];
    characters?: DerivedCharacter[];
    duration?: number;
    wordCount?: number;
    confidenceScore?: number;
  }) => void;

  // L3 actions
  setL3Status: (status: L3Status) => void;
  setL3Results: (results: L3Result[] | null) => void;
  setL3LastFetchTime: (time: number | null) => void;

  // 重置
  reset: () => void;
}

const initialState = {
  projectId: null,
  isDirty: false,
  lastSavedAt: null,
  isLoading: false,

  currentFormat: 'chinese_short' as ScriptFormat,
  currentRendering: 'cjk_zh' as TextRendering,

  viewMode: 'edit' as ViewMode,
  editorMode: 'full' as EditorMode,
  leftSidebarCollapsed: false,
  rightSidebarCollapsed: false,
  activeRightPanel: 'characters' as const,
  rightPanelLocked: false,

  derivedScenes: [] as DerivedScene[],
  derivedCharacters: [] as DerivedCharacter[],
  estimatedDuration: 0,
  wordCount: 0,
  confidenceScore: 0,

  l3Status: 'idle' as L3Status,
  l3Results: null as L3Result[] | null,
  l3LastFetchTime: null as number | null,
};

export const useEditorStore = create<EditorState>((set) => ({
  ...initialState,

  // Actions
  setProjectId: (id) => set({ projectId: id }),
  setDirty: (dirty) => set({ isDirty: dirty }),
  setLastSavedAt: (date) => set({ lastSavedAt: date }),
  setLoading: (loading) => set({ isLoading: loading }),
  setFormat: (format) => set({ currentFormat: format }),
  setRendering: (rendering) => set({ currentRendering: rendering }),
  setViewMode: (mode) => set({ viewMode: mode }),
  setEditorMode: (mode) => set({ editorMode: mode }),
  toggleLeftSidebar: () => set((state) => ({ leftSidebarCollapsed: !state.leftSidebarCollapsed })),
  toggleRightSidebar: () => set((state) => ({ rightSidebarCollapsed: !state.rightSidebarCollapsed })),
  setActiveRightPanel: (panel) => set({ activeRightPanel: panel }),
  setRightPanelLocked: (locked) => set({ rightPanelLocked: locked }),

  updateDerivation: (data) =>
    set((state) => ({
      ...(data.scenes !== undefined && { derivedScenes: data.scenes }),
      ...(data.characters !== undefined && { derivedCharacters: data.characters }),
      ...(data.duration !== undefined && { estimatedDuration: data.duration }),
      ...(data.wordCount !== undefined && { wordCount: data.wordCount }),
      ...(data.confidenceScore !== undefined && { confidenceScore: data.confidenceScore }),
    })),

  // L3 actions
  setL3Status: (status) => set({ l3Status: status }),
  setL3Results: (results) => set({ l3Results: results }),
  setL3LastFetchTime: (time) => set({ l3LastFetchTime: time }),

  reset: () => set(initialState),
}));
