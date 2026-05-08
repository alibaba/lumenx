"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Box, ChevronDown, ChevronLeft, ChevronRight, Clapperboard, FileText, Layers3, ListTodo, MapPin, Plus, Save, Sparkles, Trash2, User, Users2, Wand2 } from "lucide-react";

import { api, crudApi, type AssetAttributesPayload, type UpdateStoryBeatPayload } from "@/lib/api";
import { assetTypeTerms, messages } from "@/lib/i18n";
import { useProjectStore, type CharacterPresenceEntry, type CharacterRelationshipEdge, type Project, type StoryAnalysis, type StoryBeat } from "@/store/projectStore";

type EntityType = "character" | "scene" | "prop";

interface ScriptNode {
  type: EntityType;
  id?: string;
  name: string;
  desc: string;
  age?: string;
  gender?: string;
  clothing?: string;
  visual_weight?: number;
}

const copy = messages.modules.scriptProcessor;
const commonActions = messages.common.actions;
const EMPTY_ANALYSIS: StoryAnalysis = { summary: "", plot_points: [], scene_beats: [], character_presence: [], character_relationships: [] };

interface BeatEditorState {
  action_summary: string;
  dialogue_excerpt: string;
  storyboard_goal: string;
}

interface CreateEntityPayload {
  type: EntityType;
  name: string;
  description: string;
}

interface ApiErrorShape {
  response?: {
    data?: {
      detail?: string;
    };
  };
  message?: string;
}

interface BeatChapterGroup {
  key: string;
  title: string;
  order: number;
  chapterOrder?: number;
  chapterTitle?: string;
  beats: StoryBeat[];
}

const QUALITY_FLAG_ORDER = ["over_segmented", "title_only", "no_characters", "no_scene"] as const;

function getQualityFlagTone(flag: string) {
  if (flag === "title_only") return "border-fuchsia-400/30 bg-fuchsia-400/10 text-fuchsia-100";
  if (flag === "no_characters") return "border-amber-400/30 bg-amber-400/10 text-amber-100";
  if (flag === "no_scene") return "border-cyan-400/30 bg-cyan-400/10 text-cyan-100";
  return "border-rose-400/30 bg-rose-400/10 text-rose-100";
}

function getQualityFlagLabel(flag: string) {
  return copy.qualityFlags[flag as keyof typeof copy.qualityFlags] || flag;
}

function formatChapterLabel(order?: number | null, title?: string | null) {
  if (order == null && !title) return copy.unchapteredLabel;
  if (order == null) return title || copy.unchapteredLabel;
  if (!title) return `第${order}章`;
  if (/^\s*第?\s*\d+\s*章/.test(title)) return title;
  return `第${order}章 · ${title}`;
}

function buildBeatChapterGroups(sceneBeats: StoryBeat[]): BeatChapterGroup[] {
  const groups = new Map<string, BeatChapterGroup>();

  sceneBeats.forEach((beat) => {
    const title = formatChapterLabel(beat.chapter_order, beat.chapter_title);
    const key = beat.chapter_order != null ? `chapter-${beat.chapter_order}` : beat.chapter_title ? `chapter-${beat.chapter_title}` : "chapter-unassigned";
    const existing = groups.get(key);
    if (existing) {
      existing.beats.push(beat);
      return;
    }
    groups.set(key, {
      key,
      title,
      order: beat.chapter_order ?? Number.MAX_SAFE_INTEGER,
      chapterOrder: beat.chapter_order,
      chapterTitle: beat.chapter_title,
      beats: [beat],
    });
  });

  return Array.from(groups.values()).sort((left, right) => {
    if (left.order !== right.order) return left.order - right.order;
    return left.title.localeCompare(right.title, "zh-CN");
  });
}

function summarizeQualityFlags(sceneBeats: StoryBeat[]) {
  const counts = new Map<string, number>();
  sceneBeats.forEach((beat) => {
    (beat.quality_flags || []).forEach((flag) => {
      counts.set(flag, (counts.get(flag) || 0) + 1);
    });
  });
  return QUALITY_FLAG_ORDER
    .filter((flag) => counts.has(flag))
    .map((flag) => ({ flag, count: counts.get(flag) || 0 }));
}

function buildScriptNodes(project: Project | null): ScriptNode[] {
  if (!project) return [];
  return [
    ...project.characters.map((character) => ({ type: "character" as const, id: character.id, name: character.name, desc: character.description || "", age: character.age, gender: character.gender, clothing: character.clothing, visual_weight: character.visual_weight })),
    ...project.scenes.map((scene) => ({ type: "scene" as const, id: scene.id, name: scene.name, desc: scene.description || "", visual_weight: scene.visual_weight })),
    ...project.props.map((prop) => ({ type: "prop" as const, id: prop.id, name: prop.name, desc: prop.description || "" })),
  ];
}

function getEntityIcon(type: EntityType) {
  if (type === "character") return <User size={14} className="text-blue-400" />;
  if (type === "scene") return <MapPin size={14} className="text-green-400" />;
  return <Box size={14} className="text-yellow-400" />;
}

function extractErrorMessage(error: unknown, fallback: string): string {
  if (typeof error !== "object" || error === null) return fallback;
  const typedError = error as ApiErrorShape;
  return typedError.response?.data?.detail || typedError.message || fallback;
}

export default function ScriptProcessor() {
  const currentProject = useProjectStore((state) => state.currentProject);
  const updateProject = useProjectStore((state) => state.updateProject);
  const analyzeProject = useProjectStore((state) => state.analyzeProject);
  const isAnalyzing = useProjectStore((state) => state.isAnalyzing);

  const [script, setScript] = useState(currentProject?.originalText || "");
  const [nodes, setNodes] = useState<ScriptNode[]>([]);
  const [selectedNode, setSelectedNode] = useState<ScriptNode | null>(null);
  const [selectedBeatId, setSelectedBeatId] = useState<string | null>(null);
  const [showPanel, setShowPanel] = useState(true);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [beatDraft, setBeatDraft] = useState<BeatEditorState>({ action_summary: "", dialogue_excerpt: "", storyboard_goal: "" });
  const [isSavingBeat, setIsSavingBeat] = useState(false);
  const [isReanalyzingBeat, setIsReanalyzingBeat] = useState(false);

  useEffect(() => {
    setScript(currentProject?.originalText || "");
    setNodes(buildScriptNodes(currentProject));
  }, [currentProject]);

  const storyAnalysis = currentProject?.story_analysis ?? EMPTY_ANALYSIS;
  const sceneBeats = useMemo(() => storyAnalysis.scene_beats ?? EMPTY_ANALYSIS.scene_beats, [storyAnalysis.scene_beats]);
  const plotPoints = storyAnalysis.plot_points ?? EMPTY_ANALYSIS.plot_points;
  const characterPresence = storyAnalysis.character_presence ?? EMPTY_ANALYSIS.character_presence;
  const characterRelationships = storyAnalysis.character_relationships ?? EMPTY_ANALYSIS.character_relationships;

  useEffect(() => {
    if (!sceneBeats.length) {
      setSelectedBeatId(null);
      return;
    }
    if (!selectedBeatId || !sceneBeats.some((beat) => beat.id === selectedBeatId)) {
      setSelectedBeatId(sceneBeats[0].id);
    }
  }, [sceneBeats, selectedBeatId]);

  const selectedBeat = useMemo(() => sceneBeats.find((beat) => beat.id === selectedBeatId) || sceneBeats[0] || null, [sceneBeats, selectedBeatId]);
  const selectedBeatFrameCount = useMemo(() => {
    if (!currentProject || !selectedBeat) return 0;
    return (currentProject.frames || []).filter((frame) => frame.story_beat_id === selectedBeat.id).length;
  }, [currentProject, selectedBeat]);
  const beatDraftDirty = useMemo(() => {
    if (!selectedBeat) return false;
    return (
      beatDraft.action_summary !== (selectedBeat.action_summary || "") ||
      beatDraft.dialogue_excerpt !== (selectedBeat.dialogue_excerpt || "") ||
      beatDraft.storyboard_goal !== (selectedBeat.storyboard_goal || "")
    );
  }, [beatDraft, selectedBeat]);

  useEffect(() => {
    setBeatDraft({
      action_summary: selectedBeat?.action_summary || "",
      dialogue_excerpt: selectedBeat?.dialogue_excerpt || "",
      storyboard_goal: selectedBeat?.storyboard_goal || "",
    });
  }, [selectedBeat]);

  const handleAnalyze = async () => {
    if (!script) return;
    try {
      await analyzeProject(script);
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, copy.unknownError);
      alert(copy.analyzeFailed(errorMessage));
    }
  };

  const handleDeleteNode = async (node: ScriptNode, event: MouseEvent) => {
    event.stopPropagation();
    if (!currentProject) return;
    if (!confirm(copy.deleteConfirm(node.name))) return;
    try {
      if (node.type === "character" && node.id) await crudApi.deleteCharacter(currentProject.id, node.id);
      if (node.type === "scene" && node.id) await crudApi.deleteScene(currentProject.id, node.id);
      if (node.type === "prop" && node.id) await crudApi.deleteProp(currentProject.id, node.id);
      updateProject(currentProject.id, await api.getProject(currentProject.id));
    } catch {
      alert(copy.failedToDeleteNode);
    }
  };

  const handleCreateNode = async (data: CreateEntityPayload) => {
    if (!currentProject) return;
    const { type, ...payload } = data;
    try {
      if (type === "character") await crudApi.createCharacter(currentProject.id, payload);
      if (type === "scene") await crudApi.createScene(currentProject.id, payload);
      if (type === "prop") await crudApi.createProp(currentProject.id, payload);
      updateProject(currentProject.id, await api.getProject(currentProject.id));
      setIsCreateDialogOpen(false);
    } catch {
      alert(copy.failedToCreateNode);
    }
  };

  const handleNodeUpdate = (updatedNode: ScriptNode) => {
    setNodes((previous) => previous.map((node) => (node.id && updatedNode.id ? (node.id === updatedNode.id ? updatedNode : node) : node.name === updatedNode.name ? updatedNode : node)));
    setSelectedNode(updatedNode);
  };

  const saveBeatDraft = async () => {
    if (!currentProject || !selectedBeat) return null;
    if (!beatDraftDirty) return currentProject;

    setIsSavingBeat(true);
    try {
      const payload: UpdateStoryBeatPayload = {
        actionSummary: beatDraft.action_summary,
        dialogueExcerpt: beatDraft.dialogue_excerpt,
        storyboardGoal: beatDraft.storyboard_goal,
      };
      const updatedProject = await api.updateStoryBeat(currentProject.id, selectedBeat.id, payload);
      updateProject(currentProject.id, updatedProject);
      return updatedProject;
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, copy.unknownError);
      alert(copy.saveBeatFailed(errorMessage));
      return null;
    } finally {
      setIsSavingBeat(false);
    }
  };

  const handleSaveBeat = async () => {
    await saveBeatDraft();
  };

  const handleReanalyzeBeat = async () => {
    if (!currentProject || !selectedBeat) return;

    const savedProject = await saveBeatDraft();
    if (beatDraftDirty && !savedProject) return;

    setIsReanalyzingBeat(true);
    try {
      const updatedProject = await api.analyzeStoryboardBeat(currentProject.id, selectedBeat.id);
      updateProject(currentProject.id, updatedProject);
    } catch (error: unknown) {
      const errorMessage = extractErrorMessage(error, copy.unknownError);
      alert(copy.reanalyzeBeatFailed(errorMessage));
    } finally {
      setIsReanalyzingBeat(false);
    }
  };

  return (
    <div className="flex h-full w-full overflow-hidden">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-white/10 bg-black/20 p-4">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-bold text-white"><Sparkles className="text-primary" size={18} />{copy.title}</h2>
            <p className="mt-1 text-xs text-gray-500">从原文到剧情摘要、场次结构、角色出场表，再到实体提炼的一体化分析面板。</p>
          </div>
          <div className="flex gap-2">
            <button onClick={handleAnalyze} disabled={!script || isAnalyzing} className="glass-button flex items-center gap-2 border-primary/30 px-4 py-1.5 text-sm text-primary hover:bg-primary/10">
              {isAnalyzing ? <Wand2 className="animate-spin" size={14} /> : <Wand2 size={14} />}{isAnalyzing ? copy.analyzing : copy.analyze}
            </button>
            <button onClick={() => setShowPanel(!showPanel)} className="rounded-lg p-2 text-gray-400 hover:bg-white/10">
              {showPanel ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
            </button>
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col border-r border-white/10">
            <div className="grid gap-3 border-b border-white/10 bg-black/10 px-6 py-4 md:grid-cols-4">
              <MetricCard icon={<Users2 size={16} />} label="角色" value={currentProject?.characters.length || 0} />
              <MetricCard icon={<MapPin size={16} />} label="场景" value={currentProject?.scenes.length || 0} />
              <MetricCard icon={<Box size={16} />} label="道具" value={currentProject?.props.length || 0} />
              <MetricCard icon={<Clapperboard size={16} />} label="场次" value={sceneBeats.length} />
            </div>
            <div className="relative flex-1 p-6">
              <textarea value={script} onChange={(event) => { const value = event.target.value; setScript(value); if (currentProject) updateProject(currentProject.id, { originalText: value }); }} placeholder={copy.scriptPlaceholder} className="h-full w-full resize-none bg-transparent font-mono text-base leading-relaxed text-gray-300 focus:outline-none" spellCheck={false} />
            </div>
          </div>

          <AnimatePresence mode="popLayout">
            {showPanel ? (
              <motion.div initial={{ width: 0, opacity: 0 }} animate={{ width: 560, opacity: 1 }} exit={{ width: 0, opacity: 0 }} className="flex h-full w-[560px] flex-col bg-black/40 backdrop-blur-md">
                <div className="flex items-center justify-between border-b border-white/10 p-4">
                  <div>
                    <h3 className="font-bold text-white">{copy.panelTitle}</h3>
                    <p className="text-xs text-gray-500">{copy.panelSummary(nodes.length)} · {sceneBeats.length} 个结构化场次</p>
                  </div>
                  <button onClick={() => setIsCreateDialogOpen(true)} className="rounded-lg bg-white/10 p-1.5 text-gray-300 transition-colors hover:bg-white/20 hover:text-white" title={copy.addEntity}>
                    <Plus size={16} />
                  </button>
                </div>

                <div className="flex-1 space-y-4 overflow-y-auto p-4 custom-scrollbar">
                  <AnalysisSummaryCard summary={storyAnalysis.summary} plotPoints={plotPoints} />
                  <SceneBeatPanel
                    sceneBeats={sceneBeats}
                    selectedBeat={selectedBeat}
                    beatDraft={beatDraft}
                    beatFrameCount={selectedBeatFrameCount}
                    beatDraftDirty={beatDraftDirty}
                    isSavingBeat={isSavingBeat}
                    isReanalyzingBeat={isReanalyzingBeat}
                    onSelectBeat={setSelectedBeatId}
                    onBeatDraftChange={setBeatDraft}
                    onSaveBeat={handleSaveBeat}
                    onReanalyzeBeat={handleReanalyzeBeat}
                  />
                  <CharacterPresencePanel entries={characterPresence} relationships={characterRelationships} />
                  <EntityWorkbench nodes={nodes} selectedNode={selectedNode} onSelectNode={setSelectedNode} onDeleteNode={handleDeleteNode} onAddEntity={() => setIsCreateDialogOpen(true)} />
                </div>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>

      <AnimatePresence>{selectedNode ? <NodeDetailModal currentProject={currentProject} selectedNode={selectedNode} setSelectedNode={setSelectedNode} handleNodeUpdate={handleNodeUpdate} updateProject={updateProject} /> : null}</AnimatePresence>
      <AnimatePresence>{isCreateDialogOpen ? <CreateEntityDialog onClose={() => setIsCreateDialogOpen(false)} onCreate={handleCreateNode} /> : null}</AnimatePresence>
    </div>
  );
}

function MetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: number }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
      <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-gray-500"><span className="text-primary">{icon}</span>{label}</div>
      <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
    </div>
  );
}

function AnalysisSummaryCard({ summary, plotPoints }: { summary: string; plotPoints: string[] }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="mb-3 flex items-center gap-2 text-white"><FileText size={16} className="text-primary" /><h4 className="font-semibold">剧情摘要</h4></div>
      <p className="text-sm leading-6 text-gray-300">{summary || "重新执行文本分析后，这里会生成整体剧情摘要，帮助后续分镜和素材理解剧情主线。"}</p>
      {plotPoints.length ? (
        <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-gray-400"><ListTodo size={13} />剧情要点</div>
          <ul className="space-y-2 text-sm text-gray-300">
            {plotPoints.map((point, index) => <li key={`${point}-${index}`} className="flex gap-2"><span className="mt-0.5 text-primary">{index + 1}.</span><span className="leading-5">{point}</span></li>)}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function SceneBeatPanel({
  sceneBeats,
  selectedBeat,
  beatDraft,
  beatFrameCount,
  beatDraftDirty,
  isSavingBeat,
  isReanalyzingBeat,
  onSelectBeat,
  onBeatDraftChange,
  onSaveBeat,
  onReanalyzeBeat,
}: {
  sceneBeats: StoryBeat[];
  selectedBeat: StoryBeat | null;
  beatDraft: BeatEditorState;
  beatFrameCount: number;
  beatDraftDirty: boolean;
  isSavingBeat: boolean;
  isReanalyzingBeat: boolean;
  onSelectBeat: (id: string) => void;
  onBeatDraftChange: (value: BeatEditorState) => void;
  onSaveBeat: () => Promise<void>;
  onReanalyzeBeat: () => Promise<void>;
}) {
  const chapterGroups = useMemo(() => buildBeatChapterGroups(sceneBeats), [sceneBeats]);
  const qualitySummary = useMemo(() => summarizeQualityFlags(sceneBeats), [sceneBeats]);
  const beatRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const selectedChapterKey = useMemo(
    () => chapterGroups.find((group) => group.beats.some((beat) => beat.id === selectedBeat?.id))?.key || null,
    [chapterGroups, selectedBeat?.id],
  );
  const [expandedChapters, setExpandedChapters] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setExpandedChapters((previous) => {
      let changed = false;
      const next = { ...previous };

      chapterGroups.forEach((group) => {
        if (next[group.key] == null) {
          next[group.key] = chapterGroups.length <= 6 || group.beats.some((beat) => beat.id === selectedBeat?.id);
          changed = true;
        }
      });

      Object.keys(next).forEach((key) => {
        if (!chapterGroups.some((group) => group.key === key)) {
          delete next[key];
          changed = true;
        }
      });

      if (selectedChapterKey && !next[selectedChapterKey]) {
        next[selectedChapterKey] = true;
        changed = true;
      }

      return changed ? next : previous;
    });
  }, [chapterGroups, selectedBeat?.id, selectedChapterKey]);

  const scrollToSelectedBeat = () => {
    if (!selectedBeat) return;
    beatRefs.current[selectedBeat.id]?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <section className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="mb-3 flex items-center gap-2 text-white"><Layers3 size={16} className="text-primary" /><h4 className="font-semibold">场次列表</h4></div>
      {!sceneBeats.length ? (
        <div className="rounded-xl border border-dashed border-white/10 px-4 py-6 text-sm text-gray-500">还没有结构化场次。点击“提取实体”后，这里会把原文拆成连续场次，供分镜分析优先参考。</div>
      ) : (
        <>
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.18em] text-gray-500">{copy.diagnosticsTitle}</div>
                <p className="mt-1 text-xs leading-5 text-gray-500">{copy.diagnosticsHint}</p>
              </div>
              <button type="button" onClick={scrollToSelectedBeat} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-gray-300 transition-colors hover:bg-white/10 hover:text-white">
                {copy.locateCurrentBeat}
              </button>
            </div>
            {selectedBeat ? <p className="mt-3 text-sm text-gray-300">{copy.currentBeatLocation(formatChapterLabel(selectedBeat.chapter_order, selectedBeat.chapter_title), selectedBeat.title)}</p> : null}
            {qualitySummary.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {qualitySummary.map((entry) => (
                  <span key={entry.flag} className={`rounded-full border px-3 py-1 text-xs ${getQualityFlagTone(entry.flag)}`}>
                    {getQualityFlagLabel(entry.flag)} {entry.count}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-emerald-200">{copy.diagnosticsClean}</p>
            )}
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)]">
            <div className="rounded-2xl border border-white/10 bg-black/20 p-3">
              <div className="mb-3">
                <div className="text-sm font-semibold text-white">{copy.sceneTreeTitle}</div>
                <p className="mt-1 text-xs leading-5 text-gray-500">{copy.sceneTreeHint}</p>
              </div>
              <div className="space-y-3 max-h-[640px] overflow-y-auto pr-1 custom-scrollbar">
                {chapterGroups.map((group) => {
                  const isExpanded = expandedChapters[group.key] ?? true;
                  const hasSelected = group.beats.some((beat) => beat.id === selectedBeat?.id);
                  return (
                    <div key={group.key} className="rounded-2xl border border-white/10 bg-white/5">
                      <button
                        type="button"
                        onClick={() => setExpandedChapters((previous) => ({ ...previous, [group.key]: !isExpanded }))}
                        className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <ChevronDown size={15} className={`text-gray-400 transition-transform ${isExpanded ? "" : "-rotate-90"}`} />
                            <span className="truncate text-sm font-semibold text-white">{group.title}</span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-2 pl-7">
                            <span className="rounded-full border border-white/10 bg-black/20 px-2.5 py-1 text-[11px] text-gray-400">{copy.chapterAnchor(group.beats.length)}</span>
                            {hasSelected ? <span className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-[11px] text-primary">{copy.chapterCurrent}</span> : null}
                          </div>
                        </div>
                      </button>
                      {isExpanded ? (
                        <div className="space-y-2 border-t border-white/10 px-3 pb-3 pt-2">
                          {group.beats.map((beat) => (
                            <button
                              key={beat.id}
                              ref={(node) => {
                                beatRefs.current[beat.id] = node;
                              }}
                              type="button"
                              onClick={() => onSelectBeat(beat.id)}
                              className={`w-full rounded-2xl border px-3 py-3 text-left transition-all ${selectedBeat?.id === beat.id ? "border-primary bg-primary/10" : "border-white/10 bg-black/20 hover:bg-white/10"}`}
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="text-xs uppercase tracking-[0.2em] text-gray-500">场次 {beat.order}</div>
                                <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-gray-400">{beat.scene_name || beat.location_hint || "未标场景"}</span>
                              </div>
                              <div className="mt-2 inline-flex rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2 py-0.5 text-[10px] text-cyan-200">
                                {formatChapterLabel(beat.chapter_order, beat.chapter_title)}
                              </div>
                              <div className="mt-2 line-clamp-2 text-sm font-semibold text-white">{beat.title}</div>
                              <div className="mt-2 line-clamp-2 text-xs leading-5 text-gray-400">{beat.summary}</div>
                              {beat.quality_flags?.length ? (
                                <div className="mt-3 flex flex-wrap gap-1.5">
                                  {beat.quality_flags.map((flag) => (
                                    <span key={`${beat.id}-${flag}`} className={`rounded-full border px-2 py-0.5 text-[10px] ${getQualityFlagTone(flag)}`}>
                                      {getQualityFlagLabel(flag)}
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
            {selectedBeat ? (
              <SceneBeatDetail
                beat={selectedBeat}
                beatDraft={beatDraft}
                beatFrameCount={beatFrameCount}
                beatDraftDirty={beatDraftDirty}
                isSavingBeat={isSavingBeat}
                isReanalyzingBeat={isReanalyzingBeat}
                onBeatDraftChange={onBeatDraftChange}
                onSaveBeat={onSaveBeat}
                onReanalyzeBeat={onReanalyzeBeat}
              />
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}

function SceneBeatDetail({
  beat,
  beatDraft,
  beatFrameCount,
  beatDraftDirty,
  isSavingBeat,
  isReanalyzingBeat,
  onBeatDraftChange,
  onSaveBeat,
  onReanalyzeBeat,
}: {
  beat: StoryBeat;
  beatDraft: BeatEditorState;
  beatFrameCount: number;
  beatDraftDirty: boolean;
  isSavingBeat: boolean;
  isReanalyzingBeat: boolean;
  onBeatDraftChange: (value: BeatEditorState) => void;
  onSaveBeat: () => Promise<void>;
  onReanalyzeBeat: () => Promise<void>;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-gray-500">当前场次</div>
          <h5 className="mt-1 text-base font-semibold text-white">{beat.title}</h5>
          <p className="mt-2 text-xs text-gray-500">{copy.currentBeatLocation(formatChapterLabel(beat.chapter_order, beat.chapter_title), beat.title)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="rounded-full bg-white/10 px-3 py-1 text-xs text-gray-400">#{beat.order}</div>
          {(beat.chapter_order != null || beat.chapter_title) ? <div className="rounded-full bg-white/10 px-3 py-1 text-xs text-gray-300">{formatChapterLabel(beat.chapter_order, beat.chapter_title)}</div> : null}
          <div className="rounded-full bg-primary/10 px-3 py-1 text-xs text-primary">{copy.beatFrameCount(beatFrameCount)}</div>
        </div>
      </div>
      <p className="mt-3 text-sm leading-6 text-gray-300">{beat.summary}</p>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <DetailPill label="场景" value={beat.scene_name || beat.location_hint || "未标注"} />
        <DetailPill label="时间" value={beat.time_hint || "未标注"} />
        <DetailPill label="角色" value={beat.character_names.join("、") || "无"} />
        <DetailPill label="道具" value={beat.prop_names.join("、") || "无"} />
      </div>
      <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-4">
        <div className="text-xs uppercase tracking-[0.18em] text-gray-500">{copy.beatQualityTitle}</div>
        {beat.quality_flags?.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {beat.quality_flags.map((flag) => (
              <span key={`${beat.id}-${flag}`} className={`rounded-full border px-3 py-1 text-xs ${getQualityFlagTone(flag)}`}>
                {getQualityFlagLabel(flag)}
              </span>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-sm text-emerald-200">{copy.beatQualityNone}</p>
        )}
      </div>
      <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-gray-500">{copy.beatWorkbenchTitle}</div>
            <p className="mt-1 text-xs leading-5 text-gray-500">{copy.beatWorkbenchHint}</p>
          </div>
          {beatDraftDirty ? <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-[11px] text-amber-200">{copy.beatDirtyBadge}</span> : null}
        </div>
        <div className="mt-4 space-y-3">
          <BeatTextarea
            id={`beat-action-${beat.id}`}
            label={copy.actionSummary}
            value={beatDraft.action_summary}
            placeholder={copy.actionSummaryPlaceholder}
            onChange={(value) => onBeatDraftChange({ ...beatDraft, action_summary: value })}
          />
          <BeatTextarea
            id={`beat-dialogue-${beat.id}`}
            label={copy.dialogueExcerpt}
            value={beatDraft.dialogue_excerpt}
            placeholder={copy.dialogueExcerptPlaceholder}
            onChange={(value) => onBeatDraftChange({ ...beatDraft, dialogue_excerpt: value })}
          />
          <BeatTextarea
            id={`beat-goal-${beat.id}`}
            label={copy.storyboardGoal}
            value={beatDraft.storyboard_goal}
            placeholder={copy.storyboardGoalPlaceholder}
            onChange={(value) => onBeatDraftChange({ ...beatDraft, storyboard_goal: value })}
          />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" onClick={() => void onSaveBeat()} disabled={isSavingBeat} className="rounded-lg border border-primary/30 px-3 py-2 text-sm text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-60">
            {isSavingBeat ? copy.savingBeat : copy.saveBeat}
          </button>
          <button type="button" onClick={() => void onReanalyzeBeat()} disabled={isReanalyzingBeat} className="rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60">
            {isReanalyzingBeat ? copy.reanalyzingBeat : copy.reanalyzeBeat}
          </button>
        </div>
      </div>
      {beat.storyboard_focus ? <div className="mt-4 rounded-xl border border-primary/20 bg-primary/5 p-3"><div className="text-xs uppercase tracking-[0.18em] text-primary/80">{copy.systemStoryboardFocus}</div><p className="mt-2 text-sm leading-6 text-gray-200">{beat.storyboard_focus}</p></div> : null}
      {beat.source_excerpt ? <div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-3"><div className="text-xs uppercase tracking-[0.18em] text-gray-500">原文片段</div><p className="mt-2 text-sm leading-6 text-gray-300">{beat.source_excerpt}</p></div> : null}
    </div>
  );
}

function BeatTextarea({ id, label, value, placeholder, onChange }: { id: string; label: string; value: string; placeholder: string; onChange: (value: string) => void }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs text-gray-500">{label}</label>
      <textarea id={id} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="glass-input min-h-[76px] w-full resize-y text-sm leading-6" />
    </div>
  );
}

function DetailPill({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-3"><div className="text-[11px] uppercase tracking-[0.18em] text-gray-500">{label}</div><div className="mt-1 text-sm text-gray-200">{value}</div></div>;
}

function CharacterPresencePanel({ entries, relationships }: { entries: CharacterPresenceEntry[]; relationships: CharacterRelationshipEdge[] }) {
  const [viewMode, setViewMode] = useState<"list" | "graph">("list");

  return (
    <section className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="mb-3 flex items-center gap-2 text-white"><Users2 size={16} className="text-primary" /><h4 className="font-semibold">{copy.characterPanelTitle}</h4></div>
      {!entries.length ? <div className="rounded-xl border border-dashed border-white/10 px-4 py-6 text-sm text-gray-500">{copy.characterPresenceEmpty}</div> : <div className="space-y-3">{entries.map((entry) => <CharacterPresenceCard key={entry.character_id} entry={entry} />)}</div>}
      <div className="mt-4 rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-white">{copy.relationshipTitle}</div>
            <p className="mt-1 text-xs leading-5 text-gray-500">{copy.relationshipHint}</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-white/10 px-3 py-1 text-xs text-gray-400">{relationships.length}</span>
            <div className="rounded-xl border border-white/10 bg-black/20 p-1">
              <button type="button" onClick={() => setViewMode("list")} className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${viewMode === "list" ? "bg-primary text-white" : "text-gray-400 hover:bg-white/10 hover:text-white"}`}>
                {copy.relationshipListView}
              </button>
              <button type="button" onClick={() => setViewMode("graph")} className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${viewMode === "graph" ? "bg-primary text-white" : "text-gray-400 hover:bg-white/10 hover:text-white"}`}>
                {copy.relationshipGraphView}
              </button>
            </div>
          </div>
        </div>
        {!relationships.length ? (
          <div className="rounded-xl border border-dashed border-white/10 px-4 py-5 text-sm text-gray-500">{copy.relationshipEmpty}</div>
        ) : viewMode === "graph" ? (
          <RelationshipGraph entries={entries} relationships={relationships} />
        ) : (
          <div className="space-y-3">{relationships.map((relationship) => <CharacterRelationshipCard key={relationship.pair_id} relationship={relationship} />)}</div>
        )}
      </div>
    </section>
  );
}

function CharacterPresenceCard({ entry }: { entry: CharacterPresenceEntry }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
      <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><User size={14} className="text-blue-400" /><h5 className="font-semibold text-white">{entry.character_name}</h5></div><span className="rounded-full bg-white/10 px-3 py-1 text-xs text-gray-400">提及 {entry.mention_count} 次</span></div>
      <div className="mt-3 flex flex-wrap gap-2">{entry.scene_titles.map((sceneTitle, index) => <span key={`${entry.character_id}-${sceneTitle}-${index}`} className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-xs text-gray-300">{sceneTitle}</span>)}</div>
      {entry.highlights.length ? <div className="mt-3 rounded-xl border border-white/10 bg-black/20 p-3"><div className="mb-2 text-xs uppercase tracking-[0.18em] text-gray-500">关键段落</div><ul className="space-y-2 text-sm text-gray-300">{entry.highlights.map((highlight, index) => <li key={`${entry.character_id}-highlight-${index}`} className="leading-5">{highlight}</li>)}</ul></div> : null}
    </div>
  );
}

function CharacterRelationshipCard({ relationship }: { relationship: CharacterRelationshipEdge }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-white">{relationship.source_character_name} × {relationship.target_character_name}</div>
        <span className="rounded-full bg-primary/10 px-3 py-1 text-xs text-primary">{copy.relationshipCount(relationship.co_scene_count)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {relationship.shared_scene_titles.map((sceneTitle, index) => <span key={`${relationship.pair_id}-${sceneTitle}-${index}`} className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-gray-300">{sceneTitle}</span>)}
      </div>
      {relationship.relationship_hint ? <p className="mt-3 text-sm leading-6 text-gray-300">{relationship.relationship_hint}</p> : null}
    </div>
  );
}

function RelationshipGraph({ entries, relationships }: { entries: CharacterPresenceEntry[]; relationships: CharacterRelationshipEdge[] }) {
  const nodes = useMemo(() => entries.filter((entry) => entry.character_name).slice(0, 10), [entries]);

  const layout = useMemo(() => {
    const size = 320;
    const center = size / 2;
    const radius = size / 2 - 56;
    const positions = new Map<string, { x: number; y: number }>();

    if (nodes.length === 1) {
      positions.set(nodes[0].character_id, { x: center, y: center });
      return { size, positions };
    }

    nodes.forEach((entry, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
      positions.set(entry.character_id, {
        x: center + Math.cos(angle) * radius,
        y: center + Math.sin(angle) * radius,
      });
    });

    return { size, positions };
  }, [nodes]);

  if (nodes.length < 2 || !relationships.length) {
    return <div className="rounded-xl border border-dashed border-white/10 px-4 py-5 text-sm text-gray-500">{copy.relationshipGraphEmpty}</div>;
  }

  return (
    <div data-testid="relationship-graph" className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <p className="text-xs leading-5 text-gray-500">{copy.relationshipGraphHint}</p>
      <svg viewBox={`0 0 ${layout.size} ${layout.size}`} className="mt-4 h-[320px] w-full">
        {relationships.map((relationship) => {
          const from = layout.positions.get(relationship.source_character_id);
          const to = layout.positions.get(relationship.target_character_id);
          if (!from || !to) return null;
          return (
            <g key={relationship.pair_id}>
              <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="rgba(255,255,255,0.18)" strokeWidth={Math.min(relationship.co_scene_count + 1, 6)} />
              <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 6} fill="rgba(255,255,255,0.66)" fontSize="10" textAnchor="middle">
                {relationship.co_scene_count}
              </text>
            </g>
          );
        })}
        {nodes.map((entry) => {
          const point = layout.positions.get(entry.character_id);
          if (!point) return null;
          const radius = Math.min(16 + entry.mention_count * 1.5, 28);
          return (
            <g key={entry.character_id}>
              <circle cx={point.x} cy={point.y} r={radius} fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.22)" />
              <text x={point.x} y={point.y + 4} fill="white" fontSize="11" textAnchor="middle">
                {entry.character_name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function EntityWorkbench({ nodes, selectedNode, onSelectNode, onDeleteNode, onAddEntity }: { nodes: ScriptNode[]; selectedNode: ScriptNode | null; onSelectNode: (node: ScriptNode) => void; onDeleteNode: (node: ScriptNode, event: MouseEvent) => void; onAddEntity: () => void }) {
  const renderColumn = (type: EntityType, label: string) => {
    const items = nodes.filter((node) => node.type === type);
    return (
      <div className="rounded-2xl border border-white/10 bg-black/20 p-3">
        <div className="mb-3 flex items-center gap-2">{getEntityIcon(type)}<h4 className="text-sm font-semibold text-white">{label}</h4><span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-gray-400">{items.length}</span></div>
        <div className="space-y-2">
          {!items.length ? <div className="rounded-xl border border-dashed border-white/10 px-3 py-4 text-xs text-gray-500">暂无{label}</div> : items.map((node) => (
            <div key={node.id || `${type}-${node.name}`} role="button" tabIndex={0} onClick={() => onSelectNode(node)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelectNode(node); }} className={`group w-full cursor-pointer rounded-xl border px-3 py-3 text-left transition-all ${selectedNode?.id ? (selectedNode.id === node.id ? "border-primary bg-primary/10" : "border-white/10 bg-white/5 hover:bg-white/10") : selectedNode?.name === node.name ? "border-primary bg-primary/10" : "border-white/10 bg-white/5 hover:bg-white/10"}`}>
              <div className="mb-1 flex items-center justify-between gap-2"><div className="flex min-w-0 items-center gap-2">{getEntityIcon(node.type)}<span className="truncate text-sm font-semibold text-gray-100">{node.name}</span></div><button type="button" onClick={(event) => onDeleteNode(node, event)} className="rounded p-1 text-gray-500 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-red-500/20 hover:text-red-400" title={copy.delete}><Trash2 size={12} /></button></div>
              <p className="line-clamp-2 text-xs leading-5 text-gray-400">{node.desc || "暂无描述"}</p>
              {node.visual_weight ? <div className="mt-3 flex gap-1">{[...Array(5)].map((_, index) => <div key={`${node.name}-${index}`} className={`h-1.5 flex-1 rounded-full ${index < (node.visual_weight || 0) ? "bg-primary/90" : "bg-white/10"}`} />)}</div> : null}
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <section className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="mb-3 flex items-center justify-between gap-3"><div><h4 className="font-semibold text-white">实体工作台</h4><p className="text-xs text-gray-500">角色、场景、道具在这里分栏查看和微调，修改后会同步回项目数据。</p></div><button onClick={onAddEntity} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition-colors hover:bg-white/10 hover:text-white"><Plus size={14} className="mr-1 inline-block" />{copy.addEntity}</button></div>
      <div className="grid gap-3 md:grid-cols-3">
        {renderColumn("character", assetTypeTerms.character.label)}
        {renderColumn("scene", assetTypeTerms.scene.label)}
        {renderColumn("prop", assetTypeTerms.prop.label)}
      </div>
    </section>
  );
}

function NodeDetailModal({ currentProject, selectedNode, setSelectedNode, handleNodeUpdate, updateProject }: { currentProject: Project | null; selectedNode: ScriptNode; setSelectedNode: (node: ScriptNode | null) => void; handleNodeUpdate: (node: ScriptNode) => void; updateProject: (id: string, data: Partial<Project>) => void }) {
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setSelectedNode(null)}>
      <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }} onClick={(event) => event.stopPropagation()} className="w-[500px] overflow-hidden rounded-xl border border-white/10 bg-[#1a1a1a] shadow-2xl">
        <div className="flex items-start justify-between border-b border-white/10 p-6">
          <div><div className="mb-1 flex items-center gap-2"><span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ${selectedNode.type === "character" ? "bg-blue-500/20 text-blue-400" : selectedNode.type === "scene" ? "bg-green-500/20 text-green-400" : "bg-yellow-500/20 text-yellow-400"}`}>{assetTypeTerms[selectedNode.type].label}</span><h2 className="text-xl font-bold text-white">{selectedNode.name}</h2></div><p className="text-sm text-gray-400">{copy.detailTitle}</p></div>
          <button onClick={() => setSelectedNode(null)} className="text-gray-500 hover:text-white" title={copy.closeDetail}>✕</button>
        </div>
        <div className="space-y-4 p-6">
          <div><label className="mb-1 block text-xs text-gray-500">{copy.visualDescription}</label><textarea value={selectedNode.desc} onChange={(event) => handleNodeUpdate({ ...selectedNode, desc: event.target.value })} className="glass-input h-24 w-full resize-none text-sm" /></div>
          {selectedNode.type === "character" ? <div className="grid grid-cols-2 gap-4"><div><label className="mb-1 block text-xs text-gray-500">{copy.age}</label><input type="text" value={selectedNode.age || ""} onChange={(event) => handleNodeUpdate({ ...selectedNode, age: event.target.value })} className="glass-input w-full text-sm" placeholder={copy.agePlaceholder} /></div><div><label className="mb-1 block text-xs text-gray-500">{copy.gender}</label><input type="text" value={selectedNode.gender || ""} onChange={(event) => handleNodeUpdate({ ...selectedNode, gender: event.target.value })} className="glass-input w-full text-sm" placeholder={copy.genderPlaceholder} /></div><div className="col-span-2"><label className="mb-1 block text-xs text-gray-500">{copy.clothing}</label><input type="text" value={selectedNode.clothing || ""} onChange={(event) => handleNodeUpdate({ ...selectedNode, clothing: event.target.value })} className="glass-input w-full text-sm" placeholder={copy.clothingPlaceholder} /></div></div> : null}
          {selectedNode.type !== "prop" ? <div><label className="mb-2 block text-xs text-gray-500">{copy.visualWeight}</label><div className="flex gap-2">{[1, 2, 3, 4, 5].map((weight) => <button key={weight} onClick={() => handleNodeUpdate({ ...selectedNode, visual_weight: weight })} className={`flex-1 rounded py-2 text-xs font-bold transition-colors ${(selectedNode.visual_weight || 3) === weight ? "bg-primary text-white" : "bg-white/5 text-gray-500 hover:bg-white/10"}`}>{weight}</button>)}</div><p className="mt-1 text-center text-[10px] text-gray-600">{copy.visualWeightHint}</p></div> : null}
        </div>
        <div className="flex justify-end border-t border-white/10 bg-black/20 p-4">
          <button onClick={async () => {
            if (currentProject && selectedNode.id) {
              try {
                const attributes: AssetAttributesPayload = { description: selectedNode.desc, visual_weight: selectedNode.visual_weight };
                if (selectedNode.type === "character") { attributes.age = selectedNode.age; attributes.gender = selectedNode.gender; attributes.clothing = selectedNode.clothing; }
                updateProject(currentProject.id, await api.updateAssetAttributes(currentProject.id, selectedNode.id, selectedNode.type, attributes));
                setSelectedNode(null);
              } catch { alert(copy.saveFailed); }
            } else { setSelectedNode(null); }
          }} className="flex items-center gap-2 rounded-lg bg-primary px-6 py-2 text-sm font-bold text-white hover:bg-primary/90"><Save size={14} /> {copy.saveConfig}</button>
        </div>
      </motion.div>
    </div>
  );
}

function CreateEntityDialog({ onClose, onCreate }: { onClose: () => void; onCreate: (data: CreateEntityPayload) => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [type, setType] = useState<EntityType>("character");
  const handleSubmit = () => {
    if (!name.trim()) return alert(messages.common.messages.nameRequired);
    onCreate({ name, description: desc, type });
  };
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[400px] space-y-4 rounded-xl border border-white/10 bg-[#1a1a1a] p-6" onClick={(event) => event.stopPropagation()}>
        <h3 className="font-bold text-white">{copy.addNewEntity}</h3>
        <div className="flex gap-2 rounded-lg bg-black/20 p-1">{(["character", "scene", "prop"] as const).map((entityType) => <button key={entityType} onClick={() => setType(entityType)} className={`flex-1 rounded py-1.5 text-xs font-bold capitalize ${type === entityType ? "bg-primary text-white" : "text-gray-500 hover:text-white"}`}>{assetTypeTerms[entityType].label}</button>)}</div>
        <div><label className="text-xs text-gray-500">{copy.name}</label><input className="glass-input w-full" value={name} onChange={(event) => setName(event.target.value)} placeholder={copy.entityNamePlaceholder} /></div>
        <div><label className="text-xs text-gray-500">{copy.description}</label><textarea className="glass-input h-24 w-full resize-none" value={desc} onChange={(event) => setDesc(event.target.value)} placeholder={copy.visualDescriptionPlaceholder} /></div>
        <div className="flex justify-end gap-2 pt-2"><button onClick={onClose} className="px-4 py-2 text-xs text-gray-400 hover:text-white">{commonActions.cancel}</button><button onClick={handleSubmit} className="rounded bg-primary px-4 py-2 text-xs font-bold text-white">{copy.create}</button></div>
      </div>
    </div>
  );
}
