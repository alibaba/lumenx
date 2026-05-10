"use client";

import type React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Film, Image as ImageIcon, Link2, Loader2, Plus, RefreshCw, RotateCcw, ShieldCheck, Trash2, Unlink2, Upload, Wand2, X } from "lucide-react";
import {
    type AtelierAgentPlanContext as CoreAtelierAgentPlanContext,
    type AtelierAgentPlannerPackage,
    type AtelierAgentToolCallPayload,
    type AtelierAgentTurn,
    type AtelierApprovalMode,
    type AtelierNode,
    type AtelierVideoCandidate,
} from "@/lib/api";
import { buildReferenceLinks, findVideoDropTarget, getAtelierReferenceNodeIds as getReferenceNodeIds } from "@/lib/atelierCanvas";
import {
    getAtelierAgentPlanContext,
    getAtelierPlanContextRows,
    getAtelierPlannerPackageRows,
    isAgentTurnBlocked,
    isAtelierAgentPlanStale,
    validateAtelierAgentIntent,
    type AtelierAgentPlanContext,
} from "@/lib/atelierAgentPlanning";
import { getAssetUrl } from "@/lib/utils";
import { VIDEO_I2V_MODELS } from "@/lib/modelCatalog";
import { useAtelierStore } from "@/store/atelierStore";

type CandidateData = {
    reference_image_urls?: string[];
    reference_node_ids?: string[];
    candidates?: AtelierVideoCandidate[];
    selected_candidate_id?: string | null;
    parent_node_id?: string;
    reference_role?: string;
};

type DragState = {
    nodeId: string;
    startPointerX: number;
    startPointerY: number;
    startNodeX: number;
    startNodeY: number;
};

type ConnectDragState = {
    fromNodeId: string;
    startX: number;
    startY: number;
    pointerX: number;
    pointerY: number;
};

function getNodeData(node: AtelierNode): CandidateData {
    return node.data as CandidateData;
}

function getReferenceImages(node: AtelierNode): string[] {
    const refs = getNodeData(node).reference_image_urls;
    return Array.isArray(refs) ? refs : [];
}

function getCandidates(node: AtelierNode): AtelierVideoCandidate[] {
    const candidates = getNodeData(node).candidates;
    return Array.isArray(candidates) ? candidates : [];
}

function statusTone(status: string) {
    if (status === "completed") return "border-emerald-400/50 bg-emerald-400/10 text-emerald-200";
    if (status === "processing" || status === "pending") return "border-blue-400/50 bg-blue-400/10 text-blue-200";
    if (status === "failed") return "border-red-400/50 bg-red-400/10 text-red-200";
    return "border-white/15 bg-white/8 text-text-secondary";
}

function summarizeCandidates(candidates: AtelierVideoCandidate[]) {
    return candidates.reduce(
        (summary, candidate) => ({
            ...summary,
            [candidate.status]: summary[candidate.status] + 1,
        }),
        { pending: 0, processing: 0, completed: 0, failed: 0 }
    );
}

function formatCandidateParams(candidate: AtelierVideoCandidate) {
    const params = candidate.params ?? {};
    const duration = typeof params.duration === "number" ? `${params.duration}s` : null;
    const resolution = typeof params.resolution === "string" ? params.resolution : null;
    const mode = typeof params.generation_mode === "string" ? params.generation_mode.toUpperCase() : null;
    return [candidate.model, duration, resolution, mode].filter(Boolean).join(" · ");
}

function getCallableToolCalls(turn: AtelierAgentTurn | null): AtelierAgentToolCallPayload[] {
    return (turn?.tool_calls ?? [])
        .filter((call) => call.status === "proposed" || call.status === "approval_required")
        .map((call) => ({
            tool_name: call.tool_name,
            arguments: call.arguments,
        }));
}

function toolCallTone(status: string) {
    if (status === "completed") return "border-emerald-400/40 text-emerald-200";
    if (status === "failed" || status === "denied") return "border-red-400/40 text-red-200";
    if (status === "approval_required") return "border-amber-300/40 text-amber-100";
    return "border-white/12 text-text-secondary";
}

const APPROVAL_MODE_OPTIONS: Array<{ value: AtelierApprovalMode; label: string; description: string }> = [
    { value: "untrusted", label: "Untrusted", description: "Ask before canvas or generation actions." },
    { value: "on_failure", label: "On failure", description: "Canvas writes may run; generation still asks." },
    { value: "on_request", label: "On request", description: "Ask only for tools marked as approval-only." },
    { value: "never", label: "Never", description: "Run allowed tools within hard limits." },
];

function clearPlannedAgentTurn(
    setPreviewTurn: (turn: AtelierAgentTurn | null) => void,
    setPlannedToolCalls: (calls: AtelierAgentToolCallPayload[]) => void,
    setPlannerPackage?: (plannerPackage: AtelierAgentPlannerPackage | null) => void,
    setPlanContext?: (context: CoreAtelierAgentPlanContext | null) => void
) {
    setPreviewTurn(null);
    setPlannedToolCalls([]);
    setPlannerPackage?.(null);
    setPlanContext?.(null);
}

function getNodeCenter(node: AtelierNode) {
    return {
        x: node.x + node.width / 2,
        y: node.y + node.height / 2,
    };
}

function isImageReferencedByVideo(imageNode: AtelierNode, videoNode: AtelierNode) {
    const imageUrl = imageNode.media_urls[0];
    const videoData = getNodeData(videoNode);
    return (
        videoData.reference_node_ids?.includes(imageNode.id) ||
        Boolean(imageUrl && videoData.reference_image_urls?.includes(imageUrl))
    );
}

function isImageBoundToAnyVideo(imageNode: AtelierNode, allNodes: AtelierNode[]) {
    const parentNodeId = getNodeData(imageNode).parent_node_id;
    return Boolean(parentNodeId) || allNodes.some((node) => node.type === "video" && isImageReferencedByVideo(imageNode, node));
}

function ImageReferenceNode({
    node,
    allNodes,
    isSelected,
    onSelect,
    onDragStart,
    onConnectStart,
}: {
    node: AtelierNode;
    allNodes: AtelierNode[];
    isSelected: boolean;
    onSelect: (nodeId: string) => void;
    onDragStart: (event: React.PointerEvent, node: AtelierNode) => void;
    onConnectStart: (event: React.PointerEvent, node: AtelierNode) => void;
}) {
    const attachReferenceNode = useAtelierStore((state) => state.attachReferenceNode);
    const [isTargetPickerOpen, setIsTargetPickerOpen] = useState(false);
    const [isAttaching, setIsAttaching] = useState(false);
    const imageUrl = node.media_urls[0];
    const targetVideoNodes = isImageBoundToAnyVideo(node, allNodes)
        ? []
        : allNodes.filter((candidate) => candidate.type === "video");
    const handleAttach = async (videoNodeId: string) => {
        setIsAttaching(true);
        try {
            await attachReferenceNode(videoNodeId, node.id);
            setIsTargetPickerOpen(false);
        } finally {
            setIsAttaching(false);
        }
    };

    return (
        <div
            className={`group absolute cursor-grab overflow-hidden rounded-lg border bg-black/50 shadow-2xl active:cursor-grabbing ${
                isSelected ? "border-primary/70 ring-2 ring-primary/20" : "border-white/12"
            }`}
            style={{
                transform: `translate(${node.x}px, ${node.y}px)`,
                width: node.width,
                height: node.height,
            }}
            onPointerDown={(event) => {
                onSelect(node.id);
                onDragStart(event, node);
            }}
        >
            {imageUrl ? (
                <img src={getAssetUrl(imageUrl)} alt={node.title} className="h-full w-full object-cover" />
            ) : (
                <div className="h-full w-full grid place-items-center text-text-muted">
                    <ImageIcon size={24} />
                </div>
            )}
            <div className="absolute inset-x-0 bottom-0 bg-black/70 px-2 py-1 text-[11px] text-white/80 truncate">
                {node.title}
            </div>
            {imageUrl && (
                <div className="absolute right-1 top-1">
                    <button
                        type="button"
                        disabled={targetVideoNodes.length === 0}
                        onPointerDown={(event) => {
                            event.stopPropagation();
                            onConnectStart(event, node);
                        }}
                        onClick={(event) => {
                            event.stopPropagation();
                        }}
                        className="grid h-7 w-7 place-items-center rounded bg-black/70 text-blue-100 opacity-0 transition hover:bg-primary/80 disabled:cursor-not-allowed disabled:opacity-30 group-hover:opacity-100"
                        title="Drag to a video node"
                    >
                        <Link2 size={13} />
                    </button>
                    <button
                        type="button"
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={(event) => {
                            event.stopPropagation();
                            setIsTargetPickerOpen((value) => !value);
                        }}
                        className="mt-1 grid h-7 w-7 place-items-center rounded bg-black/70 text-white/80 opacity-0 transition hover:bg-white/15 group-hover:opacity-100"
                        title="Choose video target"
                    >
                        <Film size={13} />
                    </button>
                    {isTargetPickerOpen && (
                        <div
                            className="absolute right-0 top-8 z-50 w-48 rounded-md border border-white/10 bg-[#0b0b12]/95 p-1.5 shadow-2xl"
                            onPointerDown={(event) => event.stopPropagation()}
                        >
                            {targetVideoNodes.length === 0 ? (
                                <div className="px-2 py-2 text-[11px] text-text-muted">No video target</div>
                            ) : (
                                targetVideoNodes.map((videoNode) => (
                                    <button
                                        key={videoNode.id}
                                        type="button"
                                        disabled={isAttaching}
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            handleAttach(videoNode.id);
                                        }}
                                        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px] text-text-secondary hover:bg-white/10 disabled:opacity-40"
                                    >
                                        {isAttaching ? <Loader2 size={12} className="animate-spin" /> : <Film size={12} />}
                                        <span className="min-w-0 flex-1 truncate">{videoNode.title}</span>
                                    </button>
                                ))
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function VideoGenerationNode({
    node,
    allNodes,
    isSelected,
    onSelect,
    onDragStart,
}: {
    node: AtelierNode;
    allNodes: AtelierNode[];
    isSelected: boolean;
    onSelect: (nodeId: string) => void;
    onDragStart: (event: React.PointerEvent, node: AtelierNode) => void;
}) {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const updateNode = useAtelierStore((state) => state.updateNode);
    const uploadReferenceImage = useAtelierStore((state) => state.uploadReferenceImage);
    const attachReferenceNode = useAtelierStore((state) => state.attachReferenceNode);
    const detachReferenceNode = useAtelierStore((state) => state.detachReferenceNode);
    const createVideoCandidates = useAtelierStore((state) => state.createVideoCandidates);
    const regenerateVideoCandidates = useAtelierStore((state) => state.regenerateVideoCandidates);
    const retryCandidate = useAtelierStore((state) => state.retryCandidate);
    const selectCandidate = useAtelierStore((state) => state.selectCandidate);
    const deleteCandidate = useAtelierStore((state) => state.deleteCandidate);
    const [model, setModel] = useState(node.data?.model as string || VIDEO_I2V_MODELS[0]?.id || "wan2.7-i2v");
    const [prompt, setPrompt] = useState(node.prompt || "");
    const [duration, setDuration] = useState(5);
    const [resolution, setResolution] = useState("720p");
    const [batchSize, setBatchSize] = useState(3);
    const [isUploading, setIsUploading] = useState(false);
    const [isGenerating, setIsGenerating] = useState(false);
    const [isRegenerating, setIsRegenerating] = useState(false);
    const [isReferencePickerOpen, setIsReferencePickerOpen] = useState(false);
    const [isUpdatingReferences, setIsUpdatingReferences] = useState(false);
    const [retryingCandidateId, setRetryingCandidateId] = useState<string | null>(null);
    const references = getReferenceImages(node);
    const referenceNodeIds = getReferenceNodeIds(node);
    const referenceItems = references.map((url, index) => ({
        url,
        node: allNodes.find((candidate) => candidate.id === referenceNodeIds[index])
            ?? allNodes.find((candidate) => candidate.type === "image" && candidate.media_urls.includes(url)),
    }));
    const availableReferenceNodes = allNodes.filter((candidate) =>
        candidate.type === "image" &&
        candidate.media_urls[0] &&
        !referenceNodeIds.includes(candidate.id)
    );
    const candidates = getCandidates(node);
    const selectedCandidateId = getNodeData(node).selected_candidate_id;
    const candidateSummary = summarizeCandidates(candidates);

    const selectedCandidate = candidates.find((candidate) => candidate.id === selectedCandidateId);
    const modelConfig = VIDEO_I2V_MODELS.find((option) => option.id === model);
    const durationConfig = modelConfig?.duration;
    const durationOptions = durationConfig?.type === "buttons" ? durationConfig.options : [3, 5, 8, 10];
    const resolutionOptions = modelConfig?.params.resolution?.options ?? ["720p", "1080p"];

    const handleUpload = async (files: FileList | null) => {
        const file = files?.[0];
        if (!file) return;
        setIsUploading(true);
        try {
            await uploadReferenceImage(node.id, file);
        } finally {
            setIsUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const handleGenerate = async () => {
        if (!prompt.trim() || references.length === 0) return;
        setIsGenerating(true);
        try {
            await updateNode(node.id, { prompt, data: { ...node.data, model } });
            await createVideoCandidates(node.id, {
                prompt,
                model,
                reference_image_urls: references,
                batch_size: batchSize,
                params: {
                    duration,
                    resolution,
                    prompt_extend: true,
                    generation_mode: "i2v",
                },
            });
        } finally {
            setIsGenerating(false);
        }
    };

    const getGenerationConfig = () => ({
        prompt,
        model,
        reference_image_urls: references,
        batch_size: batchSize,
        params: {
            duration,
            resolution,
            prompt_extend: true,
            generation_mode: "i2v",
        },
    });

    const handleRegenerate = async () => {
        if (!prompt.trim() || references.length === 0) return;
        setIsRegenerating(true);
        try {
            await updateNode(node.id, { prompt, data: { ...node.data, model } });
            await regenerateVideoCandidates(node.id, getGenerationConfig());
        } finally {
            setIsRegenerating(false);
        }
    };

    const handleRetry = async (candidateId: string) => {
        setRetryingCandidateId(candidateId);
        try {
            await retryCandidate(node.id, candidateId);
        } finally {
            setRetryingCandidateId(null);
        }
    };

    const handleAttachReference = async (imageNodeId: string) => {
        setIsUpdatingReferences(true);
        try {
            await attachReferenceNode(node.id, imageNodeId);
            setIsReferencePickerOpen(false);
        } finally {
            setIsUpdatingReferences(false);
        }
    };

    const handleDetachReference = async (referenceUrl: string, imageNodeId?: string) => {
        setIsUpdatingReferences(true);
        try {
            await detachReferenceNode(node.id, referenceUrl, imageNodeId);
        } finally {
            setIsUpdatingReferences(false);
        }
    };

    return (
        <div
            className={`absolute rounded-lg border bg-[#0b0b12]/92 shadow-2xl shadow-black/40 backdrop-blur-xl ${
                isSelected ? "border-primary/70 ring-2 ring-primary/20" : "border-white/12"
            }`}
            style={{
                transform: `translate(${node.x}px, ${node.y}px)`,
                width: node.width,
                minHeight: node.height,
            }}
            onPointerDown={() => onSelect(node.id)}
        >
            <div
                className="flex cursor-grab items-center justify-between border-b border-white/10 px-4 py-3 active:cursor-grabbing"
                onPointerDown={(event) => {
                    onSelect(node.id);
                    onDragStart(event, node);
                }}
            >
                <div className="flex items-center gap-2">
                    <span className="grid h-8 w-8 place-items-center rounded-md bg-primary/20 text-primary">
                        <Film size={17} />
                    </span>
                    <div>
                        <div className="text-sm font-semibold text-foreground">{node.title}</div>
                        <div className="text-[11px] text-text-muted">Model, references, candidates</div>
                    </div>
                </div>
                <span className={`rounded-full border px-2 py-1 text-[10px] uppercase ${statusTone(node.status)}`}>
                    {node.status}
                </span>
            </div>

            <div className="space-y-4 p-4">
                <label className="block">
                    <span className="mb-1 block text-xs font-medium text-text-secondary">Prompt</span>
                    <textarea
                        value={prompt}
                        onChange={(event) => setPrompt(event.target.value)}
                        rows={3}
                        className="w-full resize-none rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-foreground outline-none focus:border-primary/60"
                    />
                </label>

                <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                        <span className="mb-1 block text-xs font-medium text-text-secondary">Model</span>
                        <select
                            value={model}
                            onChange={(event) => setModel(event.target.value)}
                            className="w-full rounded-md border border-white/10 bg-[#11111a] px-3 py-2 text-sm text-foreground outline-none"
                        >
                            {VIDEO_I2V_MODELS.map((option: (typeof VIDEO_I2V_MODELS)[number]) => (
                                <option key={option.id} value={option.id}>
                                    {option.name}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="block">
                        <span className="mb-1 block text-xs font-medium text-text-secondary">Resolution</span>
                        <select
                            value={resolution}
                            onChange={(event) => setResolution(event.target.value)}
                            className="w-full rounded-md border border-white/10 bg-[#11111a] px-3 py-2 text-sm text-foreground outline-none"
                        >
                            {resolutionOptions.map((option: string) => (
                                <option key={option} value={option}>
                                    {option}
                                </option>
                            ))}
                        </select>
                    </label>
                </div>

                <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                        <span className="mb-1 block text-xs font-medium text-text-secondary">Duration</span>
                        <select
                            value={duration}
                            onChange={(event) => setDuration(Number(event.target.value))}
                            className="w-full rounded-md border border-white/10 bg-[#11111a] px-3 py-2 text-sm text-foreground outline-none"
                        >
                            {durationOptions.map((option: number) => (
                                <option key={option} value={option}>
                                    {option}s
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="block">
                        <span className="mb-1 block text-xs font-medium text-text-secondary">Candidates</span>
                        <input
                            type="number"
                            min={1}
                            max={6}
                            value={batchSize}
                            onChange={(event) => setBatchSize(Number(event.target.value))}
                            className="w-full rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-foreground outline-none"
                        />
                    </label>
                </div>

                <div>
                    <div className="mb-2 flex items-center justify-between">
                        <span className="text-xs font-medium text-text-secondary">Reference images</span>
                        <div className="flex items-center gap-1">
                            <button
                                type="button"
                                onClick={() => setIsReferencePickerOpen((value) => !value)}
                                className="inline-flex items-center gap-1 rounded-md bg-white/10 px-2 py-1 text-xs text-foreground hover:bg-white/15"
                            >
                                <ImageIcon size={13} />
                                From canvas
                            </button>
                            <button
                                type="button"
                                onClick={() => fileInputRef.current?.click()}
                                className="inline-flex items-center gap-1 rounded-md bg-white/10 px-2 py-1 text-xs text-foreground hover:bg-white/15"
                            >
                                {isUploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                                Upload
                            </button>
                        </div>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*"
                            className="hidden"
                            onChange={(event) => handleUpload(event.target.files)}
                        />
                    </div>
                    {references.length === 0 ? (
                        <div className="rounded-md border border-dashed border-white/15 px-3 py-5 text-center text-xs text-text-muted">
                            No references
                        </div>
                    ) : (
                        <div className="grid grid-cols-3 gap-2">
                            {referenceItems.map(({ url, node: referenceNode }) => (
                                <div key={`${referenceNode?.id ?? url}-${url}`} className="group relative overflow-hidden rounded-md border border-white/10 bg-black/30">
                                    <img
                                        src={getAssetUrl(url)}
                                        alt={referenceNode?.title || "Reference"}
                                        className="aspect-video w-full object-cover"
                                    />
                                    <button
                                        type="button"
                                        disabled={isUpdatingReferences}
                                        onClick={() => handleDetachReference(url, referenceNode?.id)}
                                        className="absolute right-1 top-1 rounded bg-black/70 p-1 text-red-100 opacity-0 transition hover:bg-red-500/70 disabled:opacity-30 group-hover:opacity-100"
                                        title="Remove reference"
                                    >
                                        <Trash2 size={12} />
                                    </button>
                                    {referenceNode && (
                                        <div className="absolute inset-x-0 bottom-0 truncate bg-black/70 px-1.5 py-0.5 text-[10px] text-white/75">
                                            {referenceNode.title}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                    {isReferencePickerOpen && (
                        <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-md border border-white/10 bg-black/35 p-2">
                            {availableReferenceNodes.length === 0 ? (
                                <div className="px-2 py-2 text-xs text-text-muted">No available image nodes</div>
                            ) : (
                                availableReferenceNodes.map((candidate) => (
                                    <button
                                        key={candidate.id}
                                        type="button"
                                        disabled={isUpdatingReferences}
                                        onClick={() => handleAttachReference(candidate.id)}
                                        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-text-secondary hover:bg-white/10 disabled:opacity-40"
                                    >
                                        <img src={getAssetUrl(candidate.media_urls[0])} alt="" className="h-8 w-12 rounded object-cover" />
                                        <span className="min-w-0 flex-1 truncate">{candidate.title}</span>
                                    </button>
                                ))
                            )}
                        </div>
                    )}
                </div>

                <div className="grid grid-cols-[1fr_auto] gap-2">
                    <button
                        type="button"
                        disabled={!prompt.trim() || references.length === 0 || isGenerating}
                        onClick={handleGenerate}
                        className="flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                        {isGenerating ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                        Generate candidates
                    </button>
                    <button
                        type="button"
                        disabled={!prompt.trim() || references.length === 0 || candidates.length === 0 || isRegenerating}
                        onClick={handleRegenerate}
                        className="inline-flex items-center justify-center gap-2 rounded-md border border-white/12 bg-white/[0.05] px-3 py-2.5 text-sm font-semibold text-foreground transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-35"
                        title="Clear this round and generate a fresh candidate set"
                    >
                        {isRegenerating ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
                        Regenerate all
                    </button>
                </div>

                {selectedCandidate?.video_url && (
                    <div className="rounded-md border border-emerald-400/25 bg-emerald-400/10 p-2">
                        <div className="mb-2 text-xs font-semibold text-emerald-200">Selected result</div>
                        <video src={getAssetUrl(selectedCandidate.video_url)} controls className="aspect-video w-full rounded bg-black" />
                    </div>
                )}

                {candidates.length > 0 && (
                    <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs">
                            <span className="font-medium text-text-secondary">Candidate takes</span>
                            <span className="text-text-muted">
                                {candidateSummary.completed} ready · {candidateSummary.processing + candidateSummary.pending} running · {candidateSummary.failed} failed
                            </span>
                        </div>
                        {candidates.map((candidate) => (
                            <div key={candidate.id} className="rounded-md border border-white/10 bg-white/[0.035] p-2">
                                <div className="mb-2 flex items-center justify-between">
                                    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusTone(candidate.status)}`}>
                                        {candidate.label || candidate.id.slice(0, 8)} · {candidate.status}
                                    </span>
                                    <div className="flex items-center gap-1">
                                        <button
                                            type="button"
                                            disabled={candidate.status === "pending" || candidate.status === "processing" || retryingCandidateId === candidate.id}
                                            onClick={() => handleRetry(candidate.id)}
                                            className="rounded p-1 text-blue-200 hover:bg-blue-400/15 disabled:opacity-30"
                                            title="Retry candidate"
                                        >
                                            {retryingCandidateId === candidate.id ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                                        </button>
                                        <button
                                            type="button"
                                            disabled={candidate.status !== "completed" || !candidate.video_url}
                                            onClick={() => selectCandidate(node.id, candidate.id)}
                                            className="rounded p-1 text-emerald-200 hover:bg-emerald-400/15 disabled:opacity-30"
                                            title="Select this result"
                                        >
                                            <Check size={14} />
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => deleteCandidate(node.id, candidate.id)}
                                            className="rounded p-1 text-red-200 hover:bg-red-400/15"
                                            title="Delete candidate"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                </div>
                                <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-muted">
                                    <span>{formatCandidateParams(candidate)}</span>
                                    <span>attempt {candidate.attempt_count ?? 0}</span>
                                    {(candidate.retry_count ?? 0) > 0 && <span>retry {candidate.retry_count}</span>}
                                </div>
                                {candidate.video_url ? (
                                    <video src={getAssetUrl(candidate.video_url)} controls className="aspect-video w-full rounded bg-black" />
                                ) : (
                                    <div className="grid aspect-video w-full place-items-center rounded bg-black/60 px-3 text-center text-xs text-text-muted">
                                        {candidate.status === "failed" ? candidate.error || "Generation failed" : "Waiting"}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

function AtelierCanvas() {
    const project = useAtelierStore((state) => state.currentProject);
    const selectedNodeId = useAtelierStore((state) => state.selectedNodeId);
    const createVideoNode = useAtelierStore((state) => state.createVideoNode);
    const selectNode = useAtelierStore((state) => state.selectNode);
    const moveNodeLocal = useAtelierStore((state) => state.moveNodeLocal);
    const commitNodePosition = useAtelierStore((state) => state.commitNodePosition);
    const attachReferenceNode = useAtelierStore((state) => state.attachReferenceNode);
    const detachReferenceNode = useAtelierStore((state) => state.detachReferenceNode);
    const canvasRef = useRef<HTMLDivElement>(null);
    const dragStateRef = useRef<DragState | null>(null);
    const connectDragRef = useRef<ConnectDragState | null>(null);
    const [selectedLinkKey, setSelectedLinkKey] = useState<string | null>(null);
    const [detachingLinkKey, setDetachingLinkKey] = useState<string | null>(null);
    const [connectDrag, setConnectDrag] = useState<ConnectDragState | null>(null);
    const [connectingTargetId, setConnectingTargetId] = useState<string | null>(null);
    const nodes = useMemo(() => project?.nodes ?? [], [project?.nodes]);
    const links = useMemo(() => buildReferenceLinks(nodes), [nodes]);
    const selectedLink = links.find((link) => link.key === selectedLinkKey) ?? null;

    const getCanvasPoint = (event: React.PointerEvent) => {
        const rect = canvasRef.current?.getBoundingClientRect();
        return {
            x: event.clientX - (rect?.left ?? 0),
            y: event.clientY - (rect?.top ?? 0),
        };
    };

    const handleDragStart = (event: React.PointerEvent, node: AtelierNode) => {
        if (event.button !== 0) return;
        event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
        dragStateRef.current = {
            nodeId: node.id,
            startPointerX: event.clientX,
            startPointerY: event.clientY,
            startNodeX: node.x,
            startNodeY: node.y,
        };
    };

    const handlePointerMove = (event: React.PointerEvent) => {
        const connectState = connectDragRef.current;
        if (connectState) {
            const point = getCanvasPoint(event);
            const nextState = { ...connectState, pointerX: point.x, pointerY: point.y };
            connectDragRef.current = nextState;
            setConnectDrag(nextState);
            return;
        }
        const dragState = dragStateRef.current;
        if (!dragState) return;
        const nextX = Math.round(dragState.startNodeX + event.clientX - dragState.startPointerX);
        const nextY = Math.round(dragState.startNodeY + event.clientY - dragState.startPointerY);
        moveNodeLocal(dragState.nodeId, nextX, nextY);
    };

    const commitDrag = async (event: React.PointerEvent) => {
        const connectState = connectDragRef.current;
        if (connectState) {
            connectDragRef.current = null;
            setConnectDrag(null);
            const point = getCanvasPoint(event);
            const targetNode = findVideoDropTarget(nodes, point);
            if (targetNode) {
                setConnectingTargetId(targetNode.id);
                try {
                    await attachReferenceNode(targetNode.id, connectState.fromNodeId);
                } finally {
                    setConnectingTargetId(null);
                }
            }
            return;
        }
        const dragState = dragStateRef.current;
        if (!dragState) return;
        const nextX = Math.round(dragState.startNodeX + event.clientX - dragState.startPointerX);
        const nextY = Math.round(dragState.startNodeY + event.clientY - dragState.startPointerY);
        dragStateRef.current = null;
        await commitNodePosition(dragState.nodeId, nextX, nextY);
    };

    const handleConnectStart = (event: React.PointerEvent, node: AtelierNode) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const center = getNodeCenter(node);
        const point = getCanvasPoint(event);
        const nextState = {
            fromNodeId: node.id,
            startX: center.x,
            startY: center.y,
            pointerX: point.x,
            pointerY: point.y,
        };
        connectDragRef.current = nextState;
        setConnectDrag(nextState);
        setSelectedLinkKey(null);
        selectNode(node.id);
    };

    return (
        <div
            ref={canvasRef}
            className="relative h-full flex-1 overflow-hidden bg-[#050508]"
            onPointerMove={handlePointerMove}
            onPointerUp={commitDrag}
            onPointerCancel={() => {
                dragStateRef.current = null;
                connectDragRef.current = null;
                setConnectDrag(null);
            }}
            onPointerDown={(event) => {
                if (event.target === event.currentTarget) {
                    selectNode(null);
                    setSelectedLinkKey(null);
                }
            }}
        >
            <div
                className="absolute inset-0 opacity-40"
                style={{
                    backgroundImage:
                        "linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px)",
                    backgroundSize: "40px 40px",
                }}
            />
            <div className="absolute left-6 top-6 z-20 flex items-center gap-3">
                <button
                    type="button"
                    onClick={() => createVideoNode()}
                    className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-primary/20 hover:bg-primary/90"
                >
                    <Plus size={16} />
                    New video node
                </button>
                <span className="rounded-md border border-white/10 bg-black/35 px-3 py-2 text-xs text-text-secondary backdrop-blur">
                    {nodes.length} nodes
                </span>
            </div>
            <svg className="pointer-events-none absolute inset-0 z-10 h-full w-full overflow-visible">
                {links.map((link) => {
                    const { from, to } = link;
                    const isSelected = link.key === selectedLinkKey;
                    const fromCenter = getNodeCenter(from);
                    const toCenter = getNodeCenter(to);
                    const startX = from.x + from.width;
                    const startY = fromCenter.y;
                    const endX = to.x;
                    const endY = toCenter.y;
                    const curveOffset = Math.max(80, Math.abs(endX - startX) * 0.45);
                    const path = `M ${startX} ${startY} C ${startX + curveOffset} ${startY}, ${endX - curveOffset} ${endY}, ${endX} ${endY}`;
                    return (
                        <g key={link.key}>
                            <path
                                d={path}
                                stroke="rgba(255,255,255,0)"
                                strokeWidth="16"
                                fill="none"
                                className="pointer-events-auto cursor-pointer"
                                onPointerDown={(event) => event.stopPropagation()}
                                onClick={(event) => {
                                    event.stopPropagation();
                                    setSelectedLinkKey(link.key);
                                    selectNode(null);
                                }}
                            />
                            <path
                                d={path}
                                stroke={isSelected ? "rgba(255,0,128,0.85)" : "rgba(100,108,255,0.5)"}
                                strokeWidth={isSelected ? "3" : "2"}
                                fill="none"
                            />
                            <circle cx={startX} cy={startY} r={isSelected ? "5" : "4"} fill={isSelected ? "rgba(255,0,128,0.9)" : "rgba(100,108,255,0.85)"} />
                            <circle cx={endX} cy={endY} r={isSelected ? "5" : "4"} fill={isSelected ? "rgba(255,0,128,0.9)" : "rgba(100,108,255,0.85)"} />
                        </g>
                    );
                })}
            </svg>
            {selectedLink && (
                <div
                    className="absolute z-30 flex items-center gap-2 rounded-md border border-white/12 bg-[#0b0b12]/95 px-2 py-1.5 text-[11px] text-text-secondary shadow-xl backdrop-blur"
                    style={{
                        left: (selectedLink.from.x + selectedLink.from.width + selectedLink.to.x) / 2 - 74,
                        top: (getNodeCenter(selectedLink.from).y + getNodeCenter(selectedLink.to).y) / 2 - 18,
                    }}
                >
                    <span className="max-w-32 truncate">{selectedLink.from.title}</span>
                    <button
                        type="button"
                        disabled={detachingLinkKey === selectedLink.key}
                        onClick={async () => {
                            setDetachingLinkKey(selectedLink.key);
                            try {
                                await detachReferenceNode(selectedLink.to.id, selectedLink.url, selectedLink.from.id);
                                setSelectedLinkKey(null);
                            } finally {
                                setDetachingLinkKey(null);
                            }
                        }}
                        className="inline-flex items-center gap-1 rounded bg-red-500/15 px-2 py-1 text-red-100 hover:bg-red-500/25 disabled:opacity-40"
                        title="Detach reference"
                    >
                        {detachingLinkKey === selectedLink.key ? <Loader2 size={12} className="animate-spin" /> : <Unlink2 size={12} />}
                        Detach
                    </button>
                </div>
            )}
            {connectDrag && (
                <svg className="pointer-events-none absolute inset-0 z-30 h-full w-full overflow-visible">
                    <path
                        d={`M ${connectDrag.startX} ${connectDrag.startY} C ${connectDrag.startX + 120} ${connectDrag.startY}, ${connectDrag.pointerX - 120} ${connectDrag.pointerY}, ${connectDrag.pointerX} ${connectDrag.pointerY}`}
                        stroke="rgba(255,0,128,0.85)"
                        strokeDasharray="6 6"
                        strokeWidth="2"
                        fill="none"
                    />
                    <circle cx={connectDrag.startX} cy={connectDrag.startY} r="4" fill="rgba(255,0,128,0.9)" />
                    <circle cx={connectDrag.pointerX} cy={connectDrag.pointerY} r="4" fill="rgba(255,0,128,0.9)" />
                </svg>
            )}
            <div className="absolute inset-0 z-20">
                {nodes.map((node) =>
                    node.type === "image" ? (
                        <ImageReferenceNode
                            key={node.id}
                            node={node}
                            allNodes={nodes}
                            isSelected={selectedNodeId === node.id}
                            onSelect={selectNode}
                            onDragStart={handleDragStart}
                            onConnectStart={handleConnectStart}
                        />
                    ) : (
                        <VideoGenerationNode
                            key={node.id}
                            node={node}
                            allNodes={nodes}
                            isSelected={selectedNodeId === node.id || connectingTargetId === node.id}
                            onSelect={selectNode}
                            onDragStart={handleDragStart}
                        />
                    )
                )}
            </div>
        </div>
    );
}

function AgentPanel() {
    const project = useAtelierStore((state) => state.currentProject);
    const selectedNodeId = useAtelierStore((state) => state.selectedNodeId);
    const agentTools = useAtelierStore((state) => state.agentTools);
    const agentTurns = useAtelierStore((state) => state.agentTurns);
    const pendingAgentTurn = useAtelierStore((state) => state.pendingAgentTurn);
    const isAgentRunning = useAtelierStore((state) => state.isAgentRunning);
    const attachReferenceNode = useAtelierStore((state) => state.attachReferenceNode);
    const loadAgentTools = useAtelierStore((state) => state.loadAgentTools);
    const buildPlannerPackage = useAtelierStore((state) => state.buildPlannerPackage);
    const planAgentTurn = useAtelierStore((state) => state.planAgentTurn);
    const updateAgentPolicy = useAtelierStore((state) => state.updateAgentPolicy);
    const runAgentTurn = useAtelierStore((state) => state.runAgentTurn);
    const [attachingTargetId, setAttachingTargetId] = useState<string | null>(null);
    const [agentInput, setAgentInput] = useState("");
    const [plannedToolCalls, setPlannedToolCalls] = useState<AtelierAgentToolCallPayload[]>([]);
    const [previewTurn, setPreviewTurn] = useState<AtelierAgentTurn | null>(null);
    const [plannerPackage, setPlannerPackage] = useState<AtelierAgentPlannerPackage | null>(null);
    const [planContext, setPlanContext] = useState<CoreAtelierAgentPlanContext | null>(null);
    const [agentError, setAgentError] = useState<string | null>(null);
    const [isPolicyOpen, setIsPolicyOpen] = useState(false);
    const [isPolicyUpdating, setIsPolicyUpdating] = useState(false);
    const plannedContextRef = useRef<AtelierAgentPlanContext | null>(null);
    const selectedNode = project?.nodes.find((node) => node.id === selectedNodeId) ?? null;
    const parentNodeId = selectedNode ? getNodeData(selectedNode).parent_node_id : null;
    const parentNode = parentNodeId ? project?.nodes.find((node) => node.id === parentNodeId) : null;
    const referenceNodes = selectedNode
        ? getReferenceNodeIds(selectedNode)
            .map((nodeId) => project?.nodes.find((node) => node.id === nodeId))
            .filter((node): node is AtelierNode => Boolean(node))
        : [];
    const selectedCandidates = selectedNode ? getCandidates(selectedNode) : [];
    const selectedSummary = summarizeCandidates(selectedCandidates);
    const selectedCandidateId = selectedNode ? getNodeData(selectedNode).selected_candidate_id : null;
    const selectedCandidate = selectedCandidates.find((candidate) => candidate.id === selectedCandidateId);
    const availableVideoTargets = selectedNode?.type === "image" && !isImageBoundToAnyVideo(selectedNode, project?.nodes ?? [])
        ? (project?.nodes ?? []).filter((node) => node.type === "video")
        : [];
    const projectId = project?.id;
    const agentPolicy = project?.agent_policy;
    const allowedTools = agentPolicy?.allowed_tools ?? [];
    const allToolNames = agentTools.map((tool) => tool.name);
    const recentTurns = agentTurns.length > 0 ? [...agentTurns].slice(-5).reverse() : [];
    const activePlan = previewTurn ?? pendingAgentTurn;
    const pendingApprovalBlock = isAgentTurnBlocked(pendingAgentTurn);
    const plannerPackageRows = useMemo(() => getAtelierPlannerPackageRows(plannerPackage), [plannerPackage]);
    const planContextRows = useMemo(() => getAtelierPlanContextRows(planContext), [planContext]);
    const currentPlanContext = useMemo(
        () => getAtelierAgentPlanContext(projectId ?? null, project?.updated_at ?? null, project?.nodes.length ?? 0, selectedNode?.id ?? null, selectedNode?.updated_at ?? null),
        [projectId, project?.updated_at, project?.nodes.length, selectedNode?.id, selectedNode?.updated_at]
    );

    useEffect(() => {
        if (!projectId) return;
        loadAgentTools().catch((error) => {
            setAgentError(error instanceof Error ? error.message : "Failed to load agent tools");
        });
    }, [loadAgentTools, projectId]);

    useEffect(() => {
        if (!isAtelierAgentPlanStale(plannedContextRef.current, currentPlanContext)) return;
        clearPlannedAgentTurn(setPreviewTurn, setPlannedToolCalls, setPlannerPackage, setPlanContext);
        setAgentError(null);
        plannedContextRef.current = null;
    }, [currentPlanContext]);

    const planToolCalls = async () => {
        const validation = validateAtelierAgentIntent(agentInput);
        if (validation.error) {
            setAgentError(validation.error);
            clearPlannedAgentTurn(setPreviewTurn, setPlannedToolCalls, setPlannerPackage, setPlanContext);
            plannedContextRef.current = currentPlanContext;
            return { toolCalls: [], error: validation.error };
        }
        setPlannedToolCalls([]);
        setPlannerPackage(null);
        setPlanContext(null);
        try {
            const packageSnapshot = await buildPlannerPackage({
                user_message: agentInput,
                selected_node_id: selectedNode?.id ?? null,
            });
            setPlannerPackage(packageSnapshot);
            const plan = await planAgentTurn({
                user_message: agentInput,
                selected_node_id: selectedNode?.id ?? null,
            });
            if (plan.status === "blocked") {
                setAgentError(plan.reason);
                setPlannedToolCalls([]);
                setPlanContext(plan.context);
                plannedContextRef.current = currentPlanContext;
                return { toolCalls: [], error: plan.reason };
            }
            setAgentError(null);
            setPlannedToolCalls(plan.tool_calls);
            setPlanContext(plan.context);
            plannedContextRef.current = currentPlanContext;
            return { toolCalls: plan.tool_calls, error: null };
        } catch (error) {
            const message = error instanceof Error ? error.message : "Agent planning failed";
            setAgentError(message);
            setPlannedToolCalls([]);
            setPlanContext(null);
            plannedContextRef.current = currentPlanContext;
            return { toolCalls: [], error: message };
        }
    };

    const updatePolicy = async (
        policy: Parameters<typeof updateAgentPolicy>[0]
    ) => {
        setAgentError(null);
        setIsPolicyUpdating(true);
        try {
            await updateAgentPolicy(policy);
        } catch (error) {
            setAgentError(error instanceof Error ? error.message : "Agent policy update failed");
        } finally {
            setIsPolicyUpdating(false);
        }
    };

    const isToolAllowed = (toolName: string) => allowedTools.length === 0 || allowedTools.includes(toolName);

    const handleToggleAllowedTool = (toolName: string) => {
        if (!agentPolicy || allToolNames.length === 0) return;
        const nextAllowedTools = allowedTools.length === 0
            ? allToolNames.filter((name) => name !== toolName)
            : allowedTools.includes(toolName)
                ? allowedTools.filter((name) => name !== toolName)
                : [...allowedTools, toolName];

        if (nextAllowedTools.length === 0) {
            setAgentError("At least one tool must remain allowed; use Allow all to remove restrictions.");
            return;
        }

        updatePolicy({
            allowed_tools: nextAllowedTools.length === allToolNames.length ? [] : nextAllowedTools,
        });
    };

    const handlePreviewAgentTurn = async () => {
        setAgentError(null);
        if (pendingApprovalBlock) {
            setAgentError("Resolve the pending approval before starting a new agent turn.");
            return;
        }
        const plan = await planToolCalls();
        if (plan.error || plan.toolCalls.length === 0) return;
        try {
            const turn = await runAgentTurn({
                user_message: agentInput,
                tool_calls: plan.toolCalls,
                preview: true,
            });
            setPreviewTurn(turn);
            plannedContextRef.current = currentPlanContext;
        } catch (error) {
            setAgentError(error instanceof Error ? error.message : "Agent preview failed");
        }
    };

    const handleExecuteAgentTurn = async () => {
        setAgentError(null);
        if (pendingApprovalBlock) {
            setAgentError("Resolve the pending approval before starting a new agent turn.");
            return;
        }
        const canReusePlan = plannedToolCalls.length > 0 && !isAtelierAgentPlanStale(plannedContextRef.current, currentPlanContext);
        const plan = canReusePlan
            ? { toolCalls: plannedToolCalls, error: null }
            : await planToolCalls();
        if (plan.error || plan.toolCalls.length === 0) return;
        try {
            const turn = await runAgentTurn({
                user_message: agentInput,
                tool_calls: plan.toolCalls,
            });
            if (turn.status === "waiting_approval") {
                setPreviewTurn(turn);
                plannedContextRef.current = currentPlanContext;
            } else {
                clearPlannedAgentTurn(setPreviewTurn, setPlannedToolCalls, setPlannerPackage, setPlanContext);
                plannedContextRef.current = null;
            }
        } catch (error) {
            setAgentError(error instanceof Error ? error.message : "Agent execution failed");
        }
    };

    const handleApprovePendingTurn = async () => {
        setAgentError(null);
        const calls = getCallableToolCalls(pendingAgentTurn);
        if (calls.length === 0) return;
        try {
            await runAgentTurn({
                user_message: pendingAgentTurn?.user_message ?? agentInput,
                tool_calls: calls,
                approve: true,
                turn_id: pendingAgentTurn?.id,
            });
            clearPlannedAgentTurn(setPreviewTurn, setPlannedToolCalls, setPlannerPackage, setPlanContext);
            plannedContextRef.current = null;
        } catch (error) {
            setAgentError(error instanceof Error ? error.message : "Agent approval failed");
        }
    };

    const handleDenyPendingTurn = async () => {
        setAgentError(null);
        if (!pendingAgentTurn) return;
        try {
            await runAgentTurn({
                user_message: pendingAgentTurn.user_message || agentInput,
                tool_calls: [],
                deny: true,
                turn_id: pendingAgentTurn.id,
            });
            clearPlannedAgentTurn(setPreviewTurn, setPlannedToolCalls, setPlannerPackage, setPlanContext);
            plannedContextRef.current = null;
        } catch (error) {
            setAgentError(error instanceof Error ? error.message : "Agent denial failed");
        }
    };

    const handlePanelAttach = async (videoNodeId: string) => {
        if (!selectedNode) return;
        setAttachingTargetId(videoNodeId);
        try {
            await attachReferenceNode(videoNodeId, selectedNode.id);
        } finally {
            setAttachingTargetId(null);
        }
    };

    return (
        <aside className="w-80 overflow-y-auto border-l border-white/10 bg-[#090910]/95 p-4 backdrop-blur-xl">
            <div className="mb-4">
                <div className="text-sm font-semibold text-foreground">Agent Panel</div>
                <div className="text-xs text-text-muted">{agentTools.length} tools · {project?.agent_policy.approval_mode ?? "untrusted"}</div>
            </div>
            <div className="space-y-3 text-xs text-text-secondary">
                <div className="space-y-3 rounded-md border border-white/10 bg-white/[0.04] p-3">
                    <button
                        type="button"
                        onClick={() => setIsPolicyOpen((value) => !value)}
                        className="flex w-full items-center justify-between gap-3 text-left"
                    >
                        <span className="inline-flex items-center gap-2 text-sm font-semibold text-foreground">
                            <ShieldCheck size={14} className="text-primary" />
                            Permissions
                        </span>
                        <span className="rounded border border-white/10 bg-black/25 px-2 py-1 text-[11px] uppercase text-text-muted">
                            {agentPolicy?.approval_mode ?? "untrusted"}
                        </span>
                    </button>
                    {isPolicyOpen && agentPolicy && (
                        <div className="space-y-3">
                            <div className="grid grid-cols-2 gap-1.5">
                                {APPROVAL_MODE_OPTIONS.map((option) => {
                                    const isActive = agentPolicy.approval_mode === option.value;
                                    return (
                                        <button
                                            key={option.value}
                                            type="button"
                                            disabled={isPolicyUpdating || isActive}
                                            onClick={() => updatePolicy({ approval_mode: option.value })}
                                            className={`rounded-md border px-2 py-2 text-left transition disabled:cursor-not-allowed disabled:opacity-60 ${
                                                isActive
                                                    ? "border-primary/60 bg-primary/15 text-white"
                                                    : "border-white/10 bg-black/20 text-text-secondary hover:bg-white/10"
                                            }`}
                                        >
                                            <div className="font-semibold">{option.label}</div>
                                            <div className="mt-0.5 text-[10px] leading-snug text-text-muted">{option.description}</div>
                                        </button>
                                    );
                                })}
                            </div>
                            <label className="block">
                                <span className="mb-1 block text-[11px] font-medium text-text-secondary">Max nodes per action</span>
                                <input
                                    type="number"
                                    min={1}
                                    max={24}
                                    value={agentPolicy.max_nodes_per_action}
                                    disabled={isPolicyUpdating}
                                    onChange={(event) => {
                                        const value = Number(event.target.value);
                                        if (Number.isFinite(value)) {
                                            updatePolicy({ max_nodes_per_action: Math.max(1, Math.min(24, value)) });
                                        }
                                    }}
                                    className="w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-xs text-foreground outline-none focus:border-primary/60 disabled:opacity-50"
                                />
                            </label>
                            <div>
                                <div className="mb-1 flex items-center justify-between gap-2">
                                    <span className="text-[11px] font-medium text-text-secondary">Allowed tools</span>
                                    <button
                                        type="button"
                                        disabled={isPolicyUpdating || allowedTools.length === 0}
                                        onClick={() => updatePolicy({ allowed_tools: [] })}
                                        className="rounded border border-white/10 bg-black/20 px-2 py-1 text-[10px] text-text-muted hover:bg-white/10 disabled:opacity-40"
                                    >
                                        Allow all
                                    </button>
                                </div>
                                <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-white/10 bg-black/20 p-1.5">
                                    {agentTools.length === 0 ? (
                                        <div className="px-2 py-2 text-text-muted">Loading tools</div>
                                    ) : (
                                        agentTools.map((tool) => (
                                            <label key={tool.name} className="flex items-start gap-2 rounded px-2 py-1.5 hover:bg-white/5">
                                                <input
                                                    type="checkbox"
                                                    checked={isToolAllowed(tool.name)}
                                                    disabled={isPolicyUpdating}
                                                    onChange={() => handleToggleAllowedTool(tool.name)}
                                                    className="mt-0.5"
                                                />
                                                <span className="min-w-0 flex-1">
                                                    <span className="block truncate text-foreground">{tool.name}</span>
                                                    <span className="block text-[10px] text-text-muted">
                                                        {tool.required_permission} · cost {tool.max_count_cost}
                                                    </span>
                                                </span>
                                            </label>
                                        ))
                                    )}
                                </div>
                                <div className="mt-1 text-[10px] text-text-muted">
                                    {allowedTools.length === 0 ? "All registered Atelier tools are allowed." : `${allowedTools.length} tools allowed.`}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
                <div className="space-y-3 rounded-md border border-white/10 bg-white/[0.04] p-3">
                    <div>
                        <div className="mb-1 text-[11px] uppercase tracking-wide text-primary/80">Intent</div>
                        <textarea
                            value={agentInput}
                            onChange={(event) => {
                                setAgentInput(event.target.value);
                                clearPlannedAgentTurn(setPreviewTurn, setPlannedToolCalls, setPlannerPackage, setPlanContext);
                                plannedContextRef.current = null;
                            }}
                            rows={4}
                            className="w-full resize-none rounded-md border border-white/10 bg-black/30 px-3 py-2 text-xs text-foreground outline-none transition placeholder:text-text-muted focus:border-primary/60"
                            placeholder="Create a rain-soaked rooftop chase"
                        />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                        <button
                            type="button"
                            disabled={isAgentRunning || pendingApprovalBlock || !agentInput.trim()}
                            onClick={handlePreviewAgentTurn}
                            className="inline-flex items-center justify-center gap-2 rounded-md border border-white/12 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-foreground hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {isAgentRunning ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                            Preview
                        </button>
                        <button
                            type="button"
                            disabled={isAgentRunning || pendingApprovalBlock || !agentInput.trim()}
                            onClick={handleExecuteAgentTurn}
                            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-xs font-semibold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {isAgentRunning ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                            Execute
                        </button>
                    </div>
                    {agentError && (
                        <div className="rounded border border-red-400/30 bg-red-500/10 px-2 py-1.5 text-red-100">
                            {agentError}
                        </div>
                    )}
                    {(plannerPackageRows.length > 0 || planContextRows.length > 0) && (
                        <div className="space-y-2 rounded-md border border-white/10 bg-black/20 p-2">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-[11px] font-semibold uppercase tracking-wide text-primary/80">Planner trace</span>
                                <span className="min-w-0 truncate text-[10px] text-text-muted">{plannerPackage?.project_snapshot.title ?? project?.title}</span>
                            </div>
                            {plannerPackageRows.length > 0 && (
                                <div className="space-y-1">
                                    {plannerPackageRows.map((row) => (
                                        <div key={`package-${row.label}`} className="flex items-start justify-between gap-2 rounded border border-white/10 bg-white/[0.03] px-2 py-1">
                                            <span className="shrink-0 text-[10px] text-text-muted">{row.label}</span>
                                            <span className="min-w-0 truncate text-right text-[10px] text-text-secondary">{row.value}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {planContextRows.length > 0 && (
                                <div className="space-y-1 border-t border-white/10 pt-2">
                                    {planContextRows.map((row) => (
                                        <div key={`context-${row.label}`} className="flex items-start justify-between gap-2 px-1">
                                            <span className="shrink-0 text-[10px] text-text-muted">{row.label}</span>
                                            <span className="min-w-0 truncate text-right text-[10px] text-text-secondary">{row.value}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                    {pendingAgentTurn && (
                        <div className="rounded border border-amber-300/30 bg-amber-300/10 p-2 text-amber-50">
                            <div className="mb-2 font-medium">Approval required</div>
                            <div className="grid grid-cols-2 gap-2">
                                <button
                                    type="button"
                                    disabled={isAgentRunning}
                                    onClick={handleApprovePendingTurn}
                                    className="inline-flex w-full items-center justify-center gap-2 rounded bg-amber-300/20 px-2 py-1.5 text-amber-50 hover:bg-amber-300/30 disabled:opacity-40"
                                >
                                    {isAgentRunning ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                                    Approve
                                </button>
                                <button
                                    type="button"
                                    disabled={isAgentRunning}
                                    onClick={handleDenyPendingTurn}
                                    className="inline-flex w-full items-center justify-center gap-2 rounded border border-red-300/25 bg-red-500/15 px-2 py-1.5 text-red-100 hover:bg-red-500/25 disabled:opacity-40"
                                >
                                    {isAgentRunning ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />}
                                    Reject
                                </button>
                            </div>
                        </div>
                    )}
                    {activePlan && (
                        <div>
                            <div className="mb-1 text-[11px] font-medium text-text-secondary">Tool calls</div>
                            <div className="space-y-1">
                                {activePlan.tool_calls.map((call) => (
                                    <div key={call.call_id} className={`rounded border bg-black/20 px-2 py-1.5 ${toolCallTone(call.status)}`}>
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="min-w-0 flex-1 truncate">{call.tool_name}</span>
                                            <span className="shrink-0 uppercase">{call.status}</span>
                                        </div>
                                        {call.error && <div className="mt-1 line-clamp-2 text-red-100/80">{call.error}</div>}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    {recentTurns.length > 0 && (
                        <div>
                            <div className="mb-1 text-[11px] font-medium text-text-secondary">History</div>
                            <div className="space-y-1">
                                {recentTurns.map((turn) => (
                                    <div key={turn.id} className="rounded border border-white/10 bg-black/20 px-2 py-1.5">
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="min-w-0 flex-1 truncate text-foreground">{turn.user_message || "Agent turn"}</span>
                                            <span className="shrink-0 uppercase text-text-muted">{turn.status}</span>
                                        </div>
                                        <div className="mt-1 text-[11px] text-text-muted">
                                            {turn.preview ? "preview" : "execute"} · {turn.tool_calls.length} calls
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
                <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
                    Project: <span className="text-foreground">{project?.title || "Loading"}</span>
                </div>
                <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
                    Nodes: <span className="text-foreground">{project?.nodes.length ?? 0}</span>
                </div>
                {selectedNode ? (
                    <div className="space-y-3 rounded-md border border-primary/25 bg-primary/[0.055] p-3">
                        <div>
                            <div className="mb-1 text-[11px] uppercase tracking-wide text-primary/80">Selected node</div>
                            <div className="truncate text-sm font-semibold text-foreground">{selectedNode.title}</div>
                            <div className="mt-1 text-[11px] text-text-muted">
                                {selectedNode.type} · {selectedNode.status} · x {Math.round(selectedNode.x)}, y {Math.round(selectedNode.y)}
                            </div>
                        </div>
                        {selectedNode.prompt && (
                            <div>
                                <div className="mb-1 text-[11px] font-medium text-text-secondary">Prompt</div>
                                <div className="line-clamp-4 text-text-muted">{selectedNode.prompt}</div>
                            </div>
                        )}
                        {(parentNode || referenceNodes.length > 0) && (
                            <div>
                                <div className="mb-1 text-[11px] font-medium text-text-secondary">References</div>
                                <div className="space-y-1">
                                    {parentNode && (
                                        <div className="rounded border border-white/10 bg-black/20 px-2 py-1.5 text-foreground">
                                            {parentNode.title}
                                        </div>
                                    )}
                                    {referenceNodes.map((referenceNode) => (
                                        <div key={referenceNode.id} className="rounded border border-white/10 bg-black/20 px-2 py-1.5 text-foreground">
                                            {referenceNode.title}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                        {selectedNode.type === "video" && (
                            <div className="grid grid-cols-2 gap-2">
                                <div className="rounded border border-white/10 bg-black/20 p-2">
                                    <div className="text-[10px] uppercase text-text-muted">Ready</div>
                                    <div className="text-lg font-semibold text-emerald-200">{selectedSummary.completed}</div>
                                </div>
                                <div className="rounded border border-white/10 bg-black/20 p-2">
                                    <div className="text-[10px] uppercase text-text-muted">Running</div>
                                    <div className="text-lg font-semibold text-blue-200">{selectedSummary.processing + selectedSummary.pending}</div>
                                </div>
                                <div className="rounded border border-white/10 bg-black/20 p-2">
                                    <div className="text-[10px] uppercase text-text-muted">Failed</div>
                                    <div className="text-lg font-semibold text-red-200">{selectedSummary.failed}</div>
                                </div>
                                <div className="rounded border border-white/10 bg-black/20 p-2">
                                    <div className="text-[10px] uppercase text-text-muted">Selected</div>
                                    <div className="text-lg font-semibold text-foreground">{selectedCandidate ? "1" : "0"}</div>
                                </div>
                            </div>
                        )}
                        {selectedCandidate?.video_url && (
                            <div>
                                <div className="mb-1 text-[11px] font-medium text-text-secondary">Selected result</div>
                                <div className="truncate rounded border border-white/10 bg-black/20 px-2 py-1.5 text-text-muted">
                                    {selectedCandidate.video_url}
                                </div>
                            </div>
                        )}
                        {selectedNode.type === "image" && (
                            <div>
                                <div className="mb-1 text-[11px] font-medium text-text-secondary">Use as reference for</div>
                                <div className="space-y-1">
                                    {availableVideoTargets.length === 0 ? (
                                        <div className="rounded border border-dashed border-white/10 bg-black/20 px-2 py-2 text-text-muted">
                                            No available video node
                                        </div>
                                    ) : (
                                        availableVideoTargets.map((videoNode) => (
                                            <button
                                                key={videoNode.id}
                                                type="button"
                                                disabled={Boolean(attachingTargetId)}
                                                onClick={() => handlePanelAttach(videoNode.id)}
                                                className="flex w-full items-center gap-2 rounded border border-white/10 bg-black/20 px-2 py-1.5 text-left text-foreground hover:bg-white/10 disabled:opacity-40"
                                            >
                                                {attachingTargetId === videoNode.id ? <Loader2 size={13} className="animate-spin" /> : <Link2 size={13} />}
                                                <span className="min-w-0 flex-1 truncate">{videoNode.title}</span>
                                            </button>
                                        ))
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="rounded-md border border-dashed border-white/12 bg-white/[0.025] p-3 text-text-muted">
                        Select a node to inspect prompt, references, candidates, and canvas position.
                    </div>
                )}
            </div>
        </aside>
    );
}

function SequenceStrip() {
    const project = useAtelierStore((state) => state.currentProject);
    const selectedVideos = useMemo(() => {
        return (project?.nodes ?? []).flatMap((node) => node.media_urls.map((url) => ({ node, url })));
    }, [project?.nodes]);

    return (
        <div className="h-24 border-t border-white/10 bg-[#08080f]/95 px-4 py-3">
            <div className="mb-2 text-xs font-semibold text-text-secondary">Selected results</div>
            <div className="flex gap-2 overflow-x-auto">
                {selectedVideos.length === 0 ? (
                    <div className="text-xs text-text-muted">Select a completed candidate to add it here.</div>
                ) : (
                    selectedVideos.map(({ node, url }) => (
                        <div key={`${node.id}-${url}`} className="flex h-12 min-w-44 items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-2">
                            <Film size={14} className="text-primary" />
                            <span className="truncate text-xs text-foreground">{node.title}</span>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

export default function AtelierShell() {
    const currentProject = useAtelierStore((state) => state.currentProject);
    const ensureProject = useAtelierStore((state) => state.ensureProject);
    const createVideoNode = useAtelierStore((state) => state.createVideoNode);
    const refreshCurrentProject = useAtelierStore((state) => state.refreshCurrentProject);
    const [isBooting, setIsBooting] = useState(true);

    useEffect(() => {
        let mounted = true;
        ensureProject()
            .then(async (project) => {
                if (project.nodes.length === 0) {
                    await createVideoNode();
                }
            })
            .finally(() => {
                if (mounted) setIsBooting(false);
            });
        return () => {
            mounted = false;
        };
    }, [createVideoNode, ensureProject]);

    useEffect(() => {
        const timer = window.setInterval(() => {
            const hasRunningCandidates = currentProject?.nodes.some((node) =>
                getCandidates(node).some((candidate) => candidate.status === "pending" || candidate.status === "processing")
            );
            if (hasRunningCandidates) {
                refreshCurrentProject();
            }
        }, 3000);
        return () => window.clearInterval(timer);
    }, [currentProject?.nodes, refreshCurrentProject]);

    if (isBooting) {
        return (
            <div className="grid h-screen w-screen place-items-center bg-[#050508] text-foreground">
                <div className="flex items-center gap-3 text-sm text-text-secondary">
                    <Loader2 size={18} className="animate-spin text-primary" />
                    Opening Atelier
                </div>
            </div>
        );
    }

    return (
        <main className="flex h-screen w-screen flex-col overflow-hidden bg-[#050508] text-foreground">
            <header className="flex h-14 items-center justify-between border-b border-white/10 bg-[#08080f]/95 px-5 backdrop-blur-xl">
                <div>
                    <div className="text-sm font-semibold">LumenX Atelier</div>
                    <div className="text-[11px] text-text-muted">{currentProject?.title || "Independent canvas project"}</div>
                </div>
                <div className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-text-secondary">
                    Permission: {currentProject?.agent_policy.approval_mode || "untrusted"}
                </div>
            </header>
            <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col">
                    <AtelierCanvas />
                    <SequenceStrip />
                </div>
                <AgentPanel />
            </div>
        </main>
    );
}
