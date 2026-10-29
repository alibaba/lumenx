"use client";

import { useState, useEffect, useRef, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Paintbrush, User, Users, MapPin, Box, Lock, Unlock, RefreshCw, Upload, Image as ImageIcon, X, Check, Settings, ChevronRight, Trash2, Plus, Link as LinkIcon } from "lucide-react";
import { useProjectStore, type Project, type Scene, type Prop } from "@/store/projectStore";
import { api, crudApi } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import { confirmProjectAck, confirmRawProjectAck, ProjectReadbackError, readProjectOnce } from "@/lib/projectReadback";
import { isStudioGptImageModel } from "@/lib/studioAssetImageRequest";
import { frameCommandSnapshot, sameReadVersion } from "@/lib/frameCommandSnapshot";
import { isAssetGenerationActive, trackAssetGeneration } from "@/lib/assetGenerationOwnership";
import { useStudioAssetImageRequests, usesStudioImageCore, usesStudioQwenImageCore } from "@/lib/useStudioAssetImageRequests";
import StudioImageRequestResults from "@/components/shared/StudioImageRequestResults";
import { useStudioMotionRequests } from "@/lib/useStudioMotionRequests";
import { readStudioMotionEligibility } from "@/lib/studioMotionRequest";
import StudioMotionRequestResults from "@/components/shared/StudioMotionRequestResults";
import AssetRecoveryNotice from "@/components/shared/AssetRecoveryNotice";
import { requireActiveRuntimeEnvironment, getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import CharacterWorkbench from "./CharacterWorkbench";
import { VariantSelector } from "../common/VariantSelector";
import { VideoVariantSelector } from "../common/VideoVariantSelector";
import UploadAssetModal from "../modals/UploadAssetModal";
import StepHeader from "@/components/shared/StepHeader";
import WorkflowActionButton from "@/components/shared/WorkflowActionButton";

export default function ConsistencyVault() {
    const tv = useTranslations("vault");
    const tStep = useTranslations("stepHeader");
    const feedback = useTranslations("castWorkbench");
    const recovery = useTranslations("assetRecovery");
    const currentProject = useProjectStore((state) => state.currentProject);



    const [activeTab, setActiveTab] = useState<"character" | "scene" | "prop">("character");

    // Use global state for generation status to persist across navigation
    // Refactored to track { assetId, generationType }
    const generatingTasks = useProjectStore((state) => state.generatingTasks || []); // Fallback to empty array if not defined yet
    const addGeneratingTask = useProjectStore((state) => state.addGeneratingTask);
    const removeGeneratingTask = useProjectStore((state) => state.removeGeneratingTask);

    // Store ID and Type instead of full object to ensure reactivity
    const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
    const [selectedAssetType, setSelectedAssetType] = useState<string | null>(null);
    const [workbenchSession, setWorkbenchSession] = useState(0);

    // Create asset dialog state
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);

    // Upload modal state
    const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
    const [uploadTarget, setUploadTarget] = useState<{ id: string; type: string; name: string; description: string; projectId: string; session: object } | null>(null);
    const uploadSession = useRef<object | null>(null);
    const createSession = useRef<object | null>(null);
    const recoveryEpoch = useRef(0);
    const [readingAsset, setReadingAsset] = useState(false);
    const [assetReadFailed, setAssetReadFailed] = useState(false);
    useEffect(() => { recoveryEpoch.current += 1; setReadingAsset(false); setAssetReadFailed(false); return () => { recoveryEpoch.current += 1; }; }, [currentProject?.id, currentProject?.series_id]);
    useEffect(() => subscribeRuntimeEnvironment(() => {
        recoveryEpoch.current += 1; setReadingAsset(false); setAssetReadFailed(false);
    }), []);
    const readAssetsAgain = async () => {
        if (!currentProject) return;
        const epoch = recoveryEpoch.current;
        const environment = getRuntimeEnvironment();
        if (!environment) { setAssetReadFailed(true); return; }
        const live = () => epoch === recoveryEpoch.current && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        setReadingAsset(true); setAssetReadFailed(false);
        try { await readProjectOnce(currentProject.id, undefined, { isCurrent: snapshot => live() && snapshot.series_id === currentProject.series_id }); }
        catch { if (live()) setAssetReadFailed(true); }
        finally { if (live()) setReadingAsset(false); }
    };
    useEffect(() => {
        setSelectedAssetId(null); setSelectedAssetType(null);
        setIsCreateDialogOpen(false); setIsUploadModalOpen(false); setUploadTarget(null);
        uploadSession.current = null;
        createSession.current = null;
    }, [currentProject?.id]);

    // Derive selected asset from currentProject
    const selectedAsset = currentProject ? (() => {
        if (!selectedAssetId || !selectedAssetType) return null;
        const list = selectedAssetType === "character" ? currentProject.characters :
            selectedAssetType === "scene" ? currentProject.scenes :
                selectedAssetType === "prop" ? currentProject.props : [];
        return list?.find((a: any) => a.id === selectedAssetId) || null;
    })() : null;

    const isAssetGenerating = (assetId: string) => generatingTasks.some(task => task.assetId === assetId && isAssetGenerationActive(task));
    const getAssetGeneratingTypes = (assetId: string) => generatingTasks.filter(task => task.assetId === assetId && isAssetGenerationActive(task)).map(task => ({ type: task.generationType, batchSize: task.batchSize }));

    const handleUpdateDescription = async (assetId: string, type: string, description: string) => {
        if (!currentProject) return false;
        try {
            const response = await api.updateAssetDescription(currentProject.id, assetId, type, description);
            await confirmRawProjectAck(currentProject.id, response);
            return true;
        } catch (error) {
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : "Failed to save description");
            return false;
        }
    };

    // Each submission owns a serial status loop through navigation/unmount. A terminal
    // read is attempted once; it cannot restart polling or repeat the paid command.
    const pollTask = async (projectId: string, taskId: string, interval: number, assetId: string, type: string, isCurrent: () => boolean, ownerMatches: (snapshot: Awaited<ReturnType<typeof readProjectOnce>>) => boolean, minimumRevision?: number) => {
        for (;;) {
            await new Promise(resolve => setTimeout(resolve, interval));
            if (!isCurrent()) return;
            let status;
            try { status = await api.getTaskStatus(taskId); }
            catch {
                if (!isCurrent()) return;
                let fresh;
                try { fresh = await readProjectOnce(projectId, minimumRevision, { isCurrent: snapshot => isCurrent() && ownerMatches(snapshot) }); }
                catch { throw new Error(recovery('readFailed')); }
                if (!isCurrent()) return;
                const pool = type === 'character' ? fresh.characters : type === 'scene' ? fresh.scenes : fresh.props;
                if (pool.find(item => item.id === assetId)?.status !== 'needs_confirmation') throw new Error(recovery('readFailed'));
                return;
            }
            if (!isCurrent()) return;
            if (status?.status === 'pending' || status?.status === 'processing') continue;
            if (status?.status === 'completed' || status?.status === 'needs_confirmation') {
                await readProjectOnce(projectId, minimumRevision, { isCurrent: snapshot => isCurrent() && ownerMatches(snapshot) });
                return;
            }
            if (status?.status === 'failed') {
                alert(tv('genFailed', { error: status.error || '' }));
                try { await readProjectOnce(projectId, minimumRevision, { isCurrent: snapshot => isCurrent() && ownerMatches(snapshot) }); }
                catch { if (isCurrent()) alert(feedback('toastRefreshFailed')); }
                return;
            }
            throw new Error(recovery('readFailed'));
        }
    };

    const handleGenerate = async (assetId: string, type: string, generationType: string = "all", prompt: string = "", applyStyle: boolean = true, negativePrompt: string = "", batchSize: number = 1) => {
        if (!currentProject || useProjectStore.getState().generatingTasks.some(task => task.assetId === assetId && task.generationType === generationType && isAssetGenerationActive(task))) return;
        const projectId = currentProject.id;
        const environment = requireActiveRuntimeEnvironment();
        const source = (type === 'character' ? currentProject.characters : type === 'scene' ? currentProject.scenes : currentProject.props).find(item => item.id === assetId)?.source;
        addGeneratingTask(assetId, generationType, batchSize);
        const marker = useProjectStore.getState().generatingTasks.at(-1);
        const ownerMatches = (snapshot: Awaited<ReturnType<typeof readProjectOnce>>) => (source !== 'series' || snapshot.series_id === currentProject.series_id) && (type === 'character' ? snapshot.characters : type === 'scene' ? snapshot.scenes : snapshot.props).some(item => item.id === assetId && item.source === source);
        const isCurrent = () => {
            const store = useProjectStore.getState();
            const owner = store.currentProject?.id === projectId ? store.currentProject : store.projects.find(project => project.id === projectId);
            return isCurrentRuntimeEnvironment(environment.key, environment.generation) && store.generatingTasks.includes(marker!) && !!owner && ownerMatches(owner);
        };
        const release = trackAssetGeneration(marker!, isCurrent);
        let submitted = false;
        try {
            const response = await api.generateAsset(projectId, assetId, type, "ArtDirection",
                currentProject.art_direction?.style_config?.positive_prompt || "", generationType,
                prompt, applyStyle, negativePrompt, batchSize, currentProject.model_settings?.t2i_model);
            if (!isCurrent()) return;
            const taskId = response?._task_id;
            if (taskId !== undefined) {
                if (typeof taskId !== "string" || !taskId.trim()) throw new Error(feedback("toastGenErr"));
                const submittedProject = frameCommandSnapshot(projectId, response);
                submitted = true;
                await pollTask(projectId, taskId, 2000, assetId, type, isCurrent, ownerMatches, submittedProject.revision);
            } else {
                await confirmRawProjectAck(projectId, response, { isCurrent: snapshot => isCurrent() && ownerMatches(snapshot) });
            }
        } catch (error) {
            if (!isCurrent()) return;
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed")
                : tv(submitted ? "pollFailed" : "startGenFailed", { error: error instanceof Error ? error.message : "" }));
        } finally {
            if (isCurrent()) removeGeneratingTask(assetId, generationType);
            release();
        }
    };

    const handleDeleteAsset = async (assetId: string, type: string) => {
        if (!currentProject || !confirm(`Are you sure you want to delete this ${type}?`)) return;
        try {
            const response = type === "character" ? await crudApi.deleteCharacter(currentProject.id, assetId)
                : type === "scene" ? await crudApi.deleteScene(currentProject.id, assetId)
                : await crudApi.deleteProp(currentProject.id, assetId);
            await confirmRawProjectAck(currentProject.id, response);
        } catch (error) {
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : "Failed to delete asset");
        }
    };

    const handleCreateAsset = async (data: { name: string; description: string }) => {
        if (!currentProject) return;
        const session = createSession.current;
        try {
            const response = activeTab === "character" ? await crudApi.createCharacter(currentProject.id, data)
                : activeTab === "scene" ? await crudApi.createScene(currentProject.id, data)
                : await crudApi.createProp(currentProject.id, data);
            await confirmRawProjectAck(currentProject.id, response);
            if (createSession.current === session) setIsCreateDialogOpen(false);
        } catch (error) {
            // A valid committed ACK has already created the asset; don't leave a
            // create button inviting an accidental duplicate when only GET failed.
            if (error instanceof ProjectReadbackError && createSession.current === session) setIsCreateDialogOpen(false);
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : "Failed to create asset");
        }
    };

    // Video Handlers
    const handleGenerateVideo = async (assetId: string, type: string, prompt: string, duration: number, assetSubType: string = "full_body", audioUrl?: string) => {
        if (!currentProject) return;

        // Validate and map the assetSubType to ensure correct values are passed
        let finalAssetType: 'full_body' | 'head_shot' | 'scene' | 'prop' = 'full_body';

        // Different mappings based on the type of asset
        if (type === "scene") {
            finalAssetType = "scene";
        } else if (type === "prop") {
            finalAssetType = "prop";
        } else {
            // For character types, ensure assetSubType is valid
            if (assetSubType === "head_shot") {
                finalAssetType = "head_shot";
            } else {
                finalAssetType = "full_body";  // default to full_body
            }
        }

        // Use a more specific generation type to avoid state pollution
        const generationType = assetSubType === "head_shot" ? "video_head_shot" : "video_full_body";

        if (useProjectStore.getState().generatingTasks.some(task => task.assetId === assetId && task.generationType === generationType && isAssetGenerationActive(task))) return;
        const projectId = currentProject.id;
        const environment = requireActiveRuntimeEnvironment();
        const source = (type === 'character' ? currentProject.characters : type === 'scene' ? currentProject.scenes : currentProject.props).find(item => item.id === assetId)?.source;
        addGeneratingTask(assetId, generationType, 1);
        const marker = useProjectStore.getState().generatingTasks.at(-1);
        const ownerMatches = (snapshot: Awaited<ReturnType<typeof readProjectOnce>>) => (source !== 'series' || snapshot.series_id === currentProject.series_id) && (type === 'character' ? snapshot.characters : type === 'scene' ? snapshot.scenes : snapshot.props).some(item => item.id === assetId && item.source === source);
        const isCurrent = () => {
            const store = useProjectStore.getState();
            const owner = store.currentProject?.id === projectId ? store.currentProject : store.projects.find(project => project.id === projectId);
            return isCurrentRuntimeEnvironment(environment.key, environment.generation) && store.generatingTasks.includes(marker!) && !!owner && ownerMatches(owner);
        };
        const release = trackAssetGeneration(marker!, isCurrent);
        let submitted = false;
        try {
            const response = await api.generateMotionRef(projectId, assetId, finalAssetType, prompt, audioUrl, duration);
            if (!isCurrent()) return;
            const taskId = response?._task_id;
            if (taskId !== undefined) {
                if (typeof taskId !== "string" || !taskId.trim()) throw new Error(feedback("toastGenErr"));
                const submittedProject = frameCommandSnapshot(projectId, response);
                submitted = true;
                await pollTask(projectId, taskId, 3000, assetId, type, isCurrent, ownerMatches, submittedProject.revision);
            } else {
                await confirmRawProjectAck(projectId, response, { isCurrent: snapshot => isCurrent() && ownerMatches(snapshot) });
            }
        } catch (error) {
            if (!isCurrent()) return;
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed")
                : tv(submitted ? "pollFailed" : "startGenFailed", { error: error instanceof Error ? error.message : "" }));
        } finally {
            if (isCurrent()) removeGeneratingTask(assetId, generationType);
            release();
        }
    };

    const handleDeleteVideo = async (assetId: string, type: string, videoId: string) => {
        if (!currentProject) return;
        if (!confirm("Are you sure you want to delete this video? This action cannot be undone.")) return;

        try {
            const response = await api.deleteAssetVideo(currentProject.id, type, assetId, videoId);
            await confirmRawProjectAck(currentProject.id, response);
        } catch (error: any) {
            console.error("Failed to delete video:", error);
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : `Failed to delete video: ${error.message}`);
        }
    };

    // Sync descriptions from Script module to Assets
    const handleSyncDescriptions = async () => {
        if (!currentProject) return;

        const confirmed = confirm(
            tv("syncDescription")
        );

        if (!confirmed) return;

        try {
            const updatedProject = await api.syncDescriptions(currentProject.id);
            await confirmRawProjectAck(currentProject.id, updatedProject);
            alert(tv("syncSuccess"));
        } catch (error: any) {
            console.error("Failed to sync descriptions:", error);
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : tv("syncFailed", { error: error.message }));
        }
    };

    const handleToggleLock = async (assetId: string, type: string) => {
        if (!currentProject) return;
        try {
            const response = await api.toggleAssetLock(currentProject.id, assetId, type);
            await confirmRawProjectAck(currentProject.id, response);
        } catch (error) {
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : "Failed to update asset lock");
        }
    };

    const handleOpenUploadModal = (asset: { id: string; name: string; description: string }, type: string) => {
        if (!currentProject) return;
        const session = {};
        uploadSession.current = session;
        setUploadTarget({ id: asset.id, type, name: asset.name, description: asset.description, projectId: currentProject.id, session });
        setIsUploadModalOpen(true);
    };

    const closeUpload = () => {
        if (uploadTarget && uploadSession.current === uploadTarget.session) {
            uploadSession.current = null;
            setIsUploadModalOpen(false); setUploadTarget(null);
        }
    };
    const handleUploadComplete = async (response: unknown) => {
        if (!uploadTarget) return;
        try { await confirmProjectAck(uploadTarget.projectId, response); }
        catch (error) { alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : "Upload could not be confirmed"); }
        closeUpload();
    };

    const assets = activeTab === "character" ? currentProject?.characters :
        activeTab === "scene" ? currentProject?.scenes :
            activeTab === "prop" ? currentProject?.props : [];

    return (
        <div className="flex flex-col h-full text-foreground">
            <StepHeader
                stepNumber={3}
                totalSteps={6}
                icon={<Users />}
                englishName="Asset Library"
                title={tStep("vaultTitle")}
                subtitle={tStep("vaultSubtitle")}
            />
            {/* Tab bar + sync action */}
            <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-glass-border bg-surface">
                <div className="flex gap-2">
                    <TabButton
                        active={activeTab === "character"}
                        onClick={() => setActiveTab("character")}
                        icon={<User size={14} />}
                        label="Characters"
                        count={currentProject?.characters?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "scene"}
                        onClick={() => setActiveTab("scene")}
                        icon={<MapPin size={14} />}
                        label="Scenes"
                        count={currentProject?.scenes?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "prop"}
                        onClick={() => setActiveTab("prop")}
                        icon={<Box size={14} />}
                        label="Props"
                        count={currentProject?.props?.length || 0}
                    />
                </div>

                <WorkflowActionButton
                    variant="secondary"
                    size="sm"
                    leftIcon={<RefreshCw />}
                    onClick={handleSyncDescriptions}
                    title={tv("syncDescHint")}
                >
                    {tv("syncDesc")}
                </WorkflowActionButton>
            </div>

            {/* Content Grid */}
            {currentProject?.workflow_mode !== "i2v_legacy" && (
                <div className="mx-6 mt-4 px-4 py-3 rounded-lg bg-primary/5 border border-primary/20 flex items-start gap-3">
                    <Paintbrush size={16} className="text-primary mt-0.5 shrink-0" />
                    <div>
                        <p className="text-sm font-medium text-foreground">{tv("r2vModeActive")}</p>
                        <p className="text-xs text-text-secondary mt-0.5">
                            {tv("r2vBannerDesc")}
                        </p>
                    </div>
                </div>
            )}
            {assets?.some(asset => asset.status === 'needs_confirmation') && <AssetRecoveryNotice onRead={readAssetsAgain} reading={readingAsset} readFailed={assetReadFailed} />}
            <div className="flex-1 overflow-y-auto p-6">
                {!currentProject ? (
                    <div className="flex items-center justify-center h-full text-text-muted">
                        Loading project...
                    </div>
                ) : assets?.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-text-muted gap-4">
                        <div className="w-16 h-16 rounded-full bg-glass flex items-center justify-center">
                            {activeTab === "character" ? <User size={32} /> : activeTab === "scene" ? <MapPin size={32} /> : <Box size={32} />}
                        </div>
                        <p>No {activeTab}s found</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                        {assets?.map((asset: any) => {
                            const cardProps = {
                                asset, type: activeTab, isGenerating: isAssetGenerating(asset.id),
                                onToggleLock: () => handleToggleLock(asset.id, activeTab),
                                onClick: () => { setWorkbenchSession(session => session + 1); setSelectedAssetId(asset.id); setSelectedAssetType(activeTab); },
                                onDelete: () => handleDeleteAsset(asset.id, activeTab),
                                onUpload: () => handleOpenUploadModal(asset, activeTab),
                            };
                            return activeTab === 'character' ? <AssetCard key={asset.id} {...cardProps} onGenerate={() => handleGenerate(asset.id, activeTab)} />
                                : <ScenePropVaultItem key={`${currentProject.id}:${activeTab}:${asset.id}:${asset.source}:${currentProject.series_id}`}
                                    {...cardProps} project={currentProject} type={activeTab}
                                    selected={selectedAssetId === asset.id && selectedAssetType === activeTab}
                                    onClose={() => { setSelectedAssetId(null); setSelectedAssetType(null); }}
                                    onUpdateDescription={description => handleUpdateDescription(asset.id, activeTab, description)}
                                    onGenerate={(applyStyle, negativePrompt, batchSize) => handleGenerate(asset.id, activeTab, 'all', '', applyStyle, negativePrompt, batchSize)}
                                    onGenerateVideo={(prompt, duration) => handleGenerateVideo(asset.id, activeTab, prompt, duration, 'video')}
                                    onDeleteVideo={videoId => handleDeleteVideo(asset.id, activeTab, videoId)}
                                    isGeneratingVideo={getAssetGeneratingTypes(asset.id).some(task => task.type.startsWith('video'))} />;
                        })}
                        {/* Create New Asset Button */}
                        <motion.div
                            layout
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            onClick={() => { createSession.current = {}; setIsCreateDialogOpen(true); }}
                            className="group relative aspect-[3/4] bg-surface rounded-2xl border-2 border-dashed border-glass-border hover:border-primary/50 overflow-hidden transition-all cursor-pointer flex items-center justify-center hover:bg-glass"
                        >
                            <div className="flex flex-col items-center gap-3 text-text-secondary group-hover:text-primary transition-colors">
                                <Plus size={40} />
                                <span className="text-sm font-medium">Add {activeTab}</span>
                            </div>
                        </motion.div>
                    </div>
                )}
            </div>

            {/* Detail Modal / Workbench */}
            <AnimatePresence>
                {selectedAsset && selectedAssetId && selectedAssetType === "character" && (
                        <CharacterWorkbench
                            key={workbenchSession}
                            asset={selectedAsset}
                            onClose={() => {
                                setSelectedAssetId(null);
                                setSelectedAssetType(null);
                            }}
                            onUpdateDescription={(desc: string) => handleUpdateDescription(selectedAssetId, selectedAssetType, desc)}
                            onGenerate={(type: string, prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => handleGenerate(selectedAssetId, selectedAssetType, type, prompt, applyStyle, negativePrompt, batchSize)}
                            generatingTypes={getAssetGeneratingTypes(selectedAssetId)}
                            stylePrompt={currentProject?.art_direction?.style_config?.positive_prompt || ""}
                            styleNegativePrompt={currentProject?.art_direction?.style_config?.negative_prompt || ""}
                            onGenerateVideo={(prompt: string, duration: number, subType?: string, audioUrl?: string) => handleGenerateVideo(selectedAssetId, selectedAssetType, prompt, duration, subType || "video", audioUrl)}
                            onDeleteVideo={(videoId: string) => handleDeleteVideo(selectedAssetId, selectedAssetType, videoId)}
                        />
                )}
            </AnimatePresence>



            {/* Create Asset Dialog */}
            <AnimatePresence>
                {isCreateDialogOpen && (
                    <CreateAssetDialog
                        type={activeTab}
                        onClose={() => { createSession.current = null; setIsCreateDialogOpen(false); }}
                        onCreate={handleCreateAsset}
                    />
                )}
            </AnimatePresence>

            {/* Upload Asset Modal */}
            {uploadTarget && currentProject && (
                <UploadAssetModal
                    isOpen={isUploadModalOpen}
                    onClose={closeUpload}
                    assetId={uploadTarget.id}
                    assetType={uploadTarget.type as "character" | "scene" | "prop"}
                    assetName={uploadTarget.name}
                    defaultDescription={uploadTarget.description}
                    scriptId={uploadTarget.projectId}
                    onUploadComplete={handleUploadComplete}
                />
            )}
        </div >
    );
}

const DEFAULT_VAULT_NEGATIVE = "low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry";

type ImageControls = {
    applyStyle: boolean; setApplyStyle: (value: boolean) => void;
    negativePrompt: string; setNegativePrompt: (value: string) => void;
    onBatchSizeChange?: (value: number) => boolean;
    inheritedNegative: string; qwen: boolean; gpt: boolean;
};
type DetailProps = {
    asset: Scene | Prop; type: 'scene' | 'prop'; onClose: () => void;
    onUpdateDescription: (description: string) => Promise<boolean>;
    onGenerate: (applyStyle: boolean, negativePrompt: string, batchSize: number) => void;
    isGenerating: boolean; stylePrompt?: string; styleNegativePrompt?: string;
    onGenerateVideo: (prompt: string, duration: number) => void;
    onDeleteVideo: (id: string) => void; isGeneratingVideo: boolean;
    imageControls?: ImageControls; requestResults?: ReactNode;
    motionBlocked?: boolean; motionResults?: ReactNode; onMotionDraftChange?: () => void;
};
type VaultItemProps = Omit<DetailProps, 'stylePrompt' | 'styleNegativePrompt' | 'imageControls' | 'requestResults' | 'motionBlocked' | 'motionResults' | 'onMotionDraftChange'> & {
    project: Project; selected: boolean; onClick: () => void;
    onToggleLock: () => void; onDelete: () => void; onUpload: () => void;
};

/** One mounted item owns both visible submission controls and the original receipt view. */
function ScenePropVaultItem(props: VaultItemProps) {
    const { project, asset, type, selected, onClick } = props;
    const t = useTranslations('castWorkbench');
    const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
    const series = useProjectStore(state => state.currentSeries);
    const model = project.model_settings?.t2i_model || 'wan2.1-t2i';
    const strict = usesStudioImageCore(type, model, 'image');
    const qwen = usesStudioQwenImageCore(type, model, 'image');
    const gpt = strict && isStudioGptImageModel(model);
    const freshInputs = qwen || gpt;
    const seriesCurrent = !!series && series.id === project.series_id && (!freshInputs || series.revision === project.read_versions?.series?.revision && !project.sharedAssetsNeedRefresh);
    const style = (project.art_direction ?? (strict && seriesCurrent ? series?.art_direction : null))?.style_config;
    const requests = useStudioAssetImageRequests({ kind: type, open: true, projectId: project.id,
        assetId: asset.id, source: asset.source, seriesId: project.series_id });
    const identity = useMemo(() => ({ projectId: project.id, seriesId: project.series_id, assetId: asset.id, source: asset.source, type, environment, model }), [project.id, project.series_id, asset.id, asset.source, type, environment, model]);
    const latest = useRef(identity); latest.current = identity;
    const draftEpoch = useRef(0);
    const detailSession = useMemo(() => ({ identity, selected, motionEpoch: draftEpoch.current }), [identity, selected]);
    const latestDetail = useRef(detailSession); latestDetail.current = detailSession;
    const lifecycle = useRef<object | null>(null);
    const pending = useRef<object | null>(null);
    const [preparing, setPreparing] = useState<object | null>(null);
    const [readFailed, setReadFailed] = useState<object | null>(null);
    const [applyStyle, setApplyStyle] = useState(true);
    // Only authored/default negative text belongs in this draft; inherited style stays separate.
    const seedNegative = !freshInputs && style?.negative_prompt || DEFAULT_VAULT_NEGATIVE;
    const [negativePrompt, setNegativePrompt] = useState(seedNegative);
    const initialNegative = useRef(seedNegative); initialNegative.current = seedNegative;
    useEffect(() => {
        const lease = {}; lifecycle.current = lease;
        setApplyStyle(true); setNegativePrompt(initialNegative.current);
        return () => { if (lifecycle.current === lease) lifecycle.current = null; };
        // Draft initialization belongs to this identity, never to a later read of the same asset.
    }, [identity]);
    const detailCurrent = () => latestDetail.current === detailSession && detailSession.selected;
    const motionCurrent = (lease = lifecycle.current) => !!lease && lifecycle.current === lease
        && latest.current === identity && detailCurrent() && !!environment
        && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    const motionRequests = useStudioMotionRequests({ open: selected, projectId: project.id,
        assetId: asset.id, assetKind: type, source: asset.source, seriesId: project.series_id,
        onCreated: () => {
            if (!motionCurrent()) throw new ProjectReadbackError('Motion author session changed');
            detailSession.motionEpoch = draftEpoch.current;
        },
        readback: async (result, isCurrent) => {
            const lease = lifecycle.current, epoch = detailSession.motionEpoch;
            const current = (snapshot: Project) => motionCurrent(lease) && draftEpoch.current === epoch && isCurrent(snapshot);
            const snapshot = await readProjectOnce(result.journal.projectId, undefined, { isCurrent: current });
            if (!current(snapshot)) throw new ProjectReadbackError('Motion result is no longer current');
            return snapshot;
        } });
    const generateMotion = async (prompt: string, duration: number) => {
        const lease = lifecycle.current;
        if (!motionCurrent(lease) || !motionRequests.owner || motionRequests.busy || motionRequests.blocked || props.isGeneratingVideo) return;
        const baseline = project, epoch = draftEpoch.current;
        const sameOwner = (snapshot: Project) => snapshot.series_id === baseline.series_id
            && (type === 'scene' ? snapshot.scenes : snapshot.props).some(item => item.id === asset.id && item.source === asset.source);
        const authorCurrent = () => {
            const root = useProjectStore.getState().currentProject;
            return motionCurrent(lease) && draftEpoch.current === epoch && !!root && sameReadVersion(root, baseline)
                && !root.sharedAssetsNeedRefresh && sameOwner(root);
        };
        setReadFailed(null);
        await motionRequests.submit(async () => {
            try {
                const fresh = await readProjectOnce(baseline.id, undefined, { isCurrent: snapshot => authorCurrent() && sameOwner(snapshot) });
                if (!authorCurrent() || !sameReadVersion(fresh, baseline)) throw new ProjectReadbackError('Motion source changed');
                const qualification = await readStudioMotionEligibility({ ...motionRequests.owner!, assetType: type, isCurrent: authorCurrent });
                if (!authorCurrent()) throw new ProjectReadbackError('Motion draft changed');
                if (!qualification.eligible) {
                    const legacy = ['model_unsupported', 'configured_audio_present', 'selected_image_required'].includes(qualification.reason ?? '')
                        || qualification.reason === 'source_kind_unsupported' && ['remote_url', 'object_key'].includes(qualification.source_kind);
                    if (!legacy) throw new ProjectReadbackError('Motion source cannot be confirmed');
                    return 'legacy';
                }
                const selectedImage = (type === 'scene' ? fresh.scenes : fresh.props).find(item => item.id === asset.id)?.image_asset?.selected_id;
                if (selectedImage !== qualification.selected_image_id) throw new ProjectReadbackError('Motion image changed');
                return { input: { asset_id: asset.id, asset_type: type, prompt, duration, batch_size: 1,
                    expected_target: qualification.expected_target }, selectedImageId: qualification.selected_image_id! };
            } catch (error) { if (motionCurrent(lease)) setReadFailed(identity); throw error; }
        }, authorCurrent, () => props.onGenerateVideo(prompt, duration));
    };
    const busy = props.isGenerating || requests.busy || preparing === identity;
    const negativeBlocked = (negative: string, useStyle: boolean, inherited: string) => qwen
        ? Array.from([negative, useStyle ? inherited : ''].filter(Boolean).join(', ')).length > 500
        : !!negative || !gpt && useStyle && !!inherited;
    const generate = async (useStyle: boolean, negative: string, batchSize: number, detail?: object) => {
        if (detail && latestDetail.current !== detail) return;
        if (!strict) { props.onGenerate(useStyle, negative, batchSize); return; }
        const lease = lifecycle.current, epoch = draftEpoch.current;
        if (!environment || !lease || pending.current === lease || busy) return;
        if (negativeBlocked(negative, useStyle, style?.negative_prompt || '')) { onClick(); return; }
        const live = () => lifecycle.current === lease && latest.current === identity
            && (!freshInputs || draftEpoch.current === epoch && (!detail || latestDetail.current === detail))
            && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        const ownerMatches = (snapshot: Project) => (asset.source !== 'series' && !freshInputs || snapshot.series_id === project.series_id)
            && (type === 'scene' ? snapshot.scenes : snapshot.props).some(item => item.id === asset.id && item.source === asset.source)
            && (snapshot.model_settings?.t2i_model || 'wan2.1-t2i') === model;
        pending.current = lease; setPreparing(identity); setReadFailed(null);
        try {
            const fresh = await readProjectOnce(project.id, undefined, { isCurrent: snapshot => live() && ownerMatches(snapshot) });
            if (!live()) return;
            if (freshInputs && useStyle && !fresh.art_direction && fresh.series_id) {
                const inherited = await api.getSeries(fresh.series_id);
                if (!live()) return;
                if (!useProjectStore.getState().applySeriesReadSnapshot(fresh.id, fresh.series_id, inherited)) throw new ProjectReadbackError('Series style is not current');
            }
            const styleAtSubmission = () => {
                const state = useProjectStore.getState(), root = state.currentProject, inherited = state.currentSeries;
                return root?.id === fresh.id ? root.art_direction ?? (inherited?.id === root.series_id ? inherited?.art_direction : null) : null;
            };
            const freshStyle = (freshInputs ? styleAtSubmission() : fresh.art_direction ?? (series && series.id === fresh.series_id ? series.art_direction : null))?.style_config;
            if (negativeBlocked(negative, useStyle, freshStyle?.negative_prompt || '')) { onClick(); return; }
            const capturedStyle = JSON.stringify(useStyle ? styleAtSubmission() : null);
            const submissionCurrent = () => {
                const state = useProjectStore.getState(), root = state.currentProject, inherited = state.currentSeries;
                if (!live() || !root || root.id !== fresh.id || (gpt ? !sameReadVersion(root, fresh) : root.revision !== fresh.revision) || !ownerMatches(root)
                    || root.sharedAssetsNeedRefresh || root.readEnvironment !== environment.key) return false;
                if (useStyle && !root.art_direction && root.series_id
                    && (!inherited || inherited.id !== root.series_id || inherited.revision !== root.read_versions?.series?.revision)) return false;
                return capturedStyle === JSON.stringify(useStyle ? styleAtSubmission() : null);
            };
            if (freshInputs && !submissionCurrent()) return;
            if (useProjectStore.getState().generatingTasks.some(task => task.assetId === asset.id && isAssetGenerationActive(task))) return;
            await requests.submit({ asset_id: asset.id, asset_type: type, generation_type: 'all',
                style_preset: 'ArtDirection', style_prompt: freshInputs && !useStyle ? '' : freshStyle?.positive_prompt || '',
                apply_style: useStyle, negative_prompt: negative, batch_size: batchSize, model_name: model, prompt: '' }, freshInputs ? submissionCurrent : undefined);
        } catch { if (live()) setReadFailed(identity); }
        finally {
            if (pending.current === lease) { pending.current = null; if (latest.current === identity && lifecycle.current === lease) setPreparing(null); }
        }
    };
    const closeDetail = () => { if (detailCurrent()) { draftEpoch.current += 1; props.onClose(); } };
    const results = <>{gpt && (!selected || applyStyle) && !!style?.negative_prompt && <p role="status" className="text-xs text-text-secondary">{t('gptNegativeNotApplied')}</p>}{readFailed === identity && <p role="status" className="text-xs text-amber-300">{t('toastRefreshFailed')}</p>}<StudioImageRequestResults requests={requests} /></>;
    return <div className="min-w-0 space-y-2">
        <AssetCard {...props} isGenerating={busy} onGenerate={() => { void generate(true, '', 1); }} />
        {!selected && results}
        <AnimatePresence>{selected && <CharacterDetailModal {...props} onClose={closeDetail} isGenerating={busy}
            onGenerate={(useStyle, negative, batch) => { if (detailCurrent()) void generate(useStyle, negative, batch, detailSession); }}
            stylePrompt={style?.positive_prompt || ''} styleNegativePrompt={style?.negative_prompt || ''}
            imageControls={strict ? { applyStyle, setApplyStyle: value => { if (detailCurrent()) { draftEpoch.current += 1; setApplyStyle(value); } },
                negativePrompt, setNegativePrompt: value => { if (detailCurrent()) { draftEpoch.current += 1; setNegativePrompt(value); } },
                onBatchSizeChange: freshInputs ? () => { if (!detailCurrent()) return false; draftEpoch.current += 1; return true; } : undefined,
                inheritedNegative: style?.negative_prompt || '', qwen, gpt } : undefined}
            requestResults={results} onGenerateVideo={(prompt, duration) => { void generateMotion(prompt, duration); }}
            isGeneratingVideo={props.isGeneratingVideo || motionRequests.busy} motionBlocked={motionRequests.blocked}
            onMotionDraftChange={() => { if (detailCurrent()) draftEpoch.current += 1; }}
            motionResults={<StudioMotionRequestResults requests={motionRequests} />} />}</AnimatePresence>
    </div>;
}

function CharacterDetailModal({ asset, type, onClose, onUpdateDescription, onGenerate, isGenerating, stylePrompt = "", styleNegativePrompt = "", onGenerateVideo, onDeleteVideo, isGeneratingVideo, imageControls, requestResults, motionBlocked, motionResults, onMotionDraftChange }: DetailProps) {
    const feedback = useTranslations("castWorkbench");
    const [description, setDescription] = useState(asset.description);
    const [isEditing, setIsEditing] = useState(false);
    const currentProject = useProjectStore((state) => state.currentProject);

    // Style Controls
    const [legacyApplyStyle, setLegacyApplyStyle] = useState(true);
    const [legacyNegative, setLegacyNegative] = useState(styleNegativePrompt || DEFAULT_VAULT_NEGATIVE);
    const applyStyle = imageControls?.applyStyle ?? legacyApplyStyle;
    const setApplyStyle = imageControls?.setApplyStyle ?? setLegacyApplyStyle;
    const negativePrompt = imageControls?.negativePrompt ?? legacyNegative;
    const setNegativePrompt = imageControls?.setNegativePrompt ?? setLegacyNegative;
    const negativeBlocked = !!imageControls && (imageControls.qwen
        ? Array.from([negativePrompt, applyStyle ? imageControls.inheritedNegative : ''].filter(Boolean).join(', ')).length > 500
        : !!negativePrompt || !imageControls.gpt && applyStyle && !!imageControls.inheritedNegative);
    const [showAdvanced, setShowAdvanced] = useState(false);

    // Video Controls
    const [activeTab, setActiveTab] = useState<"image" | "video">("image");
    const [videoPrompt, setVideoPrompt] = useState(asset.video_prompt || "");
    const videoPromptEdited = useRef(false);

    // Sync local state if asset changes
    useEffect(() => {
        setDescription(asset.description);
        if (!videoPromptEdited.current && asset.video_prompt) setVideoPrompt(asset.video_prompt);
        else if (!videoPromptEdited.current && !videoPrompt) {
            setVideoPrompt(`Cinematic shot of ${asset.name}, ${asset.description}, looking around, breathing, slight movement, high quality, 4k`);
        }
    }, [asset]);

    // Only legacy drafts inherit later style text; explicit strict corrections stay cleared.
    const strictControls = !!imageControls;
    useEffect(() => {
        if (!strictControls && styleNegativePrompt) {
            setLegacyNegative(previous => !previous || previous.includes("low quality") ? styleNegativePrompt : previous);
        }
    }, [styleNegativePrompt, strictControls]);

    const handleSave = async () => {
        if (await onUpdateDescription(description)) setIsEditing(false);
    };

    const handleSelectVariant = async (variantId: string) => {
        if (!currentProject) return;
        onMotionDraftChange?.();
        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, asset.id, type, variantId);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
        }
    };

    const handleDeleteVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, asset.id, type, variantId);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
        }
    };

    const handleGenerateClick = (batchSize: number) => {
        if (!negativeBlocked) onGenerate(applyStyle, negativePrompt, batchSize);
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-5xl h-[85vh] flex overflow-hidden shadow-lg"
            >
                {/* Left: Variant Selector */}
                <div className="w-1/2 bg-surface relative border-r border-glass-border flex flex-col overflow-hidden">
                    {/* Tab Switcher */}
                    <div className="flex border-b border-glass-border bg-surface">
                        <button
                            onClick={() => setActiveTab("image")}
                            className={`flex-1 p-3 text-sm font-bold transition-colors ${activeTab === "image" ? "text-foreground border-b-2 border-primary bg-glass" : "text-text-muted hover:text-text-secondary"}`}
                        >
                            Image Reference
                        </button>
                        <button
                            onClick={() => setActiveTab("video")}
                            className={`flex-1 p-3 text-sm font-bold transition-colors ${activeTab === "video" ? "text-foreground border-b-2 border-primary bg-glass" : "text-text-muted hover:text-text-secondary"}`}
                        >
                            Video Reference
                        </button>
                    </div>

                    <div className="flex-1 p-4 overflow-hidden">
                        {activeTab === "image" ? (
                            <VariantSelector
                                asset={asset.image_asset}
                                currentImageUrl={asset.image_url}
                                onSelect={handleSelectVariant}
                                onDelete={handleDeleteVariant}
                                onGenerate={handleGenerateClick}
                                onBatchSizeChange={imageControls?.onBatchSizeChange}
                                isGenerating={isGenerating}
                                generateDisabled={negativeBlocked}
                                aspectRatio="16:9"
                                className="h-full"
                            />
                        ) : (
                            <VideoVariantSelector
                                videos={asset.video_assets || []}
                                onDelete={onDeleteVideo}
                                onGenerate={(duration) => onGenerateVideo(videoPrompt, duration)}
                                isGenerating={isGeneratingVideo}
                                generateDisabled={motionBlocked}
                                aspectRatio="16:9"
                                className="h-full"
                            />
                        )}
                    </div>
                    {activeTab === "video" && motionResults}
                </div>

                {/* Right: Details */}
                <div className="w-1/2 flex flex-col">
                    {/* Header */}
                    <div className="p-6 border-b border-glass-border flex justify-between items-center bg-surface">
                        <h2 className="text-2xl font-bold text-foreground">{asset.name}</h2>
                        <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-full text-text-secondary hover:text-foreground">
                            <X size={24} />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="flex-1 p-6 overflow-y-auto space-y-6">
                        {requestResults}
                        {imageControls && !imageControls.qwen && negativePrompt && <div className="space-y-2 text-xs text-amber-300">
                            <p role="status">{feedback('negativeDraftUnsupported')}</p>
                            <button onClick={() => setNegativePrompt('')} className="text-primary underline">{feedback('clearNegativeForRequest')}</button>
                        </div>}
                        {imageControls && !imageControls.qwen && !imageControls.gpt && applyStyle && imageControls.inheritedNegative && <div className="space-y-2 text-xs text-amber-300">
                            <p role="status">{feedback('negativeUnsupported')}</p>
                            <button onClick={() => setApplyStyle(false)} className="text-primary underline">{feedback('disableStyleForRequest')}</button>
                        </div>}
                        {imageControls?.qwen && negativeBlocked && <p role="status" className="text-xs text-amber-300">{feedback('imageNegativeTooLong')}</p>}
                        {/* Description */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-bold text-text-secondary uppercase">Description</label>
                                {!isEditing && (
                                    <button onClick={() => setIsEditing(true)} className="text-xs text-primary hover:underline">
                                        Edit
                                    </button>
                                )}
                            </div>
                            {isEditing ? (
                                <div className="space-y-2">
                                    <textarea
                                        value={description}
                                        onChange={(e) => { onMotionDraftChange?.(); setDescription(e.target.value); }}
                                        className="w-full h-32 bg-input-bg border border-glass-border rounded-lg p-3 text-sm text-text-secondary resize-none focus:border-primary focus:outline-none"
                                    />
                                    <div className="flex justify-end gap-2">
                                        <button onClick={() => { setIsEditing(false); setDescription(asset.description); }} className="px-3 py-1.5 text-xs text-text-secondary hover:text-foreground">Cancel</button>
                                        <button onClick={handleSave} className="px-3 py-1.5 bg-primary text-white text-xs rounded hover:bg-primary/90">Save Description</button>
                                    </div>
                                </div>
                            ) : (
                                <p className="text-sm text-text-secondary leading-relaxed bg-glass p-3 rounded-lg border border-transparent hover:border-glass-border transition-colors">
                                    {asset.description}
                                </p>
                            )}
                        </div>

                        {/* Video Prompt (Only visible in Video Tab) */}
                        {activeTab === "video" && (
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-text-secondary uppercase">Video Prompt</label>
                                <textarea
                                    value={videoPrompt}
                                    onChange={(e) => { videoPromptEdited.current = true; onMotionDraftChange?.(); setVideoPrompt(e.target.value); }}
                                    className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-sm text-text-secondary resize-none focus:border-primary focus:outline-none"
                                    placeholder="Describe the motion..."
                                />
                            </div>
                        )}

                        {/* Style Control (Only visible in Image Tab) */}
                        {activeTab === "image" && (
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-text-secondary uppercase">Style Settings</label>
                                <div className="bg-glass rounded-lg p-3 border border-border-subtle">
                                    <div className="flex items-center gap-2 mb-2">
                                        <input
                                            type="checkbox"
                                            id="applyStyleModal"
                                            checked={applyStyle}
                                            onChange={(e) => setApplyStyle(e.target.checked)}
                                            className="rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary"
                                        />
                                        <label htmlFor="applyStyleModal" className="text-sm font-bold text-text-secondary cursor-pointer select-none">
                                            Apply Art Direction Style
                                        </label>
                                    </div>

                                    {stylePrompt && (
                                        <div className="text-xs text-text-muted font-mono bg-surface p-2 rounded border border-border-subtle">
                                            <span className="text-primary font-bold">Style:</span> {stylePrompt}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Advanced Settings (Negative Prompt) - Only visible in Image Tab */}
                        {activeTab === "image" && (
                            <div className="space-y-2">
                                <button
                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                    className="flex items-center gap-2 text-xs font-bold text-text-muted hover:text-foreground transition-colors uppercase"
                                >
                                    <span>Advanced Settings (Negative Prompt)</span>
                                    <ChevronRight size={12} className={`transform transition-transform ${showAdvanced ? 'rotate-90' : ''}`} />
                                </button>

                                <AnimatePresence>
                                    {showAdvanced && (
                                        <motion.div
                                            initial={{ height: 0, opacity: 0 }}
                                            animate={{ height: "auto", opacity: 1 }}
                                            exit={{ height: 0, opacity: 0 }}
                                            className="overflow-hidden"
                                        >
                                            <textarea
                                                value={negativePrompt}
                                                onChange={(e) => setNegativePrompt(e.target.value)}
                                                className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono"
                                                placeholder="Enter negative prompt..."
                                            />
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        )}
                    </div>

                    {/* Footer Actions */}
                    <div className="p-6 border-t border-glass-border bg-surface flex gap-4">
                        <button
                            onClick={onClose}
                            className="flex-1 py-3 bg-green-600 hover:bg-green-500 text-foreground rounded-xl font-bold flex items-center justify-center gap-2 shadow-lg shadow-green-500/20"
                        >
                            <Check size={18} />
                            Done
                        </button>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}

function TabButton({ active, onClick, icon, label, count }: any) {
    return (
        <button
            onClick={onClick}
            className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 border transition-colors ${active
                ? "bg-[rgba(100,108,255,0.12)] text-foreground border-primary"
                : "bg-glass text-text-secondary hover:text-foreground border-glass-border hover:border-glass-border-strong"
                }`}
        >
            <span className={active ? "text-primary" : ""}>{icon}</span>
            <span className="font-mono text-[0.65625rem] font-semibold uppercase tracking-[0.14em]">{label}</span>
            <span className={`font-mono text-[0.5625rem] px-1.5 py-0.5 rounded-full border ${
                active
                    ? "text-primary border-primary/40 bg-[rgba(100,108,255,0.08)]"
                    : "text-text-muted border-glass-border bg-black/30"
            }`}>{count}</span>
        </button>
    );
}

function ImageWithRetry({ src, alt, className }: { src: string, alt: string, className?: string }) {
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(false);
    const [retryCount, setRetryCount] = useState(0);

    // Reset state when src changes
    useEffect(() => {
        setIsLoading(true);
        setError(false);
        setRetryCount(0);
    }, [src]);

    useEffect(() => {
        if (error && retryCount < 10) {
            const timer = setTimeout(() => {
                setRetryCount(prev => prev + 1);
                setError(false);
            }, 1000 * (retryCount + 1)); // Exponential backoff
            return () => clearTimeout(timer);
        }
    }, [error, retryCount]);

    // Construct src with retry param to bypass cache if retrying
    const displaySrc = retryCount > 0 ? `${src}${src.includes('?') ? '&' : '?'}retry=${retryCount}` : src;

    return (
        <div className={`relative ${className}`}>
            {isLoading && (
                <div className="absolute inset-0 flex items-center justify-center bg-surface z-10">
                    <RefreshCw className="animate-spin text-text-secondary" size={24} />
                </div>
            )}
            <img
                src={displaySrc}
                alt={alt}
                className={`${className} ${isLoading ? 'opacity-0' : 'opacity-100'} transition-opacity duration-300`}
                onLoad={() => setIsLoading(false)}
                onError={() => {
                    setError(true);
                    setIsLoading(true); // Keep showing loader while retrying
                }}
            />
            {error && retryCount >= 10 && (
                <div className="absolute inset-0 flex items-center justify-center bg-red-500/10 backdrop-blur-sm z-20">
                    <span className="text-xs text-red-400 font-bold">Failed to load</span>
                </div>
            )}
        </div>
    );
}

function AssetCard({ asset, type, isGenerating, onGenerate, onToggleLock, onClick, onDelete, onUpload }: any) {
    const tv = useTranslations("vault");
    const recovery = useTranslations("assetRecovery");
    const isLocked = asset.locked || false;

    const imageUrl = (type === 'character' ? (asset.avatar_url || asset.image_url) : asset.image_url);
    const fullImageUrl = getAssetUrl(imageUrl);

    return (
        <motion.div
            layout
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={onClick}
            className={`group relative aspect-[3/4] bg-surface rounded-2xl border overflow-hidden transition-colors cursor-pointer ${isLocked ? 'border-primary/60 border-dashed' : 'border-glass-border hover:border-primary/50'
                }`}
        >
            {/* Image Area */}
            <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/60 z-10" />

            {imageUrl ? (
                <ImageWithRetry
                    src={fullImageUrl}
                    alt={asset.name}
                    className="w-full h-full object-cover"
                />
            ) : (
                <div className="w-full h-full flex items-center justify-center bg-glass">
                    <ImageIcon className="text-text-muted" size={48} />
                </div>
            )}

            {/* Loading Overlay */}
            {isGenerating && (
                <div className="absolute inset-0 z-20 bg-overlay backdrop-blur-sm flex items-center justify-center flex-col gap-2">
                    <RefreshCw className="animate-spin text-primary" size={32} />
                    <span className="text-xs font-mono text-primary">Generating...</span>
                </div>
            )}

            {/* Top Actions Overlay */}
            <div className="absolute top-2 right-2 z-30 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onDelete();
                    }}
                    className="p-2 rounded-full backdrop-blur-md bg-red-500/20 text-red-400 hover:bg-red-500/40 transition-colors"
                    title="Delete"
                >
                    <Trash2 size={14} />
                </button>
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onToggleLock();
                    }}
                    title={isLocked ? "Unlock" : "Lock"}
                    className={`p-2 rounded-full backdrop-blur-md transition-colors ${isLocked
                        ? "bg-primary/20 text-primary hover:bg-primary/30"
                        : "bg-surface text-foreground hover:bg-hover-bg"
                        }`}
                >
                    {isLocked ? <Lock size={14} /> : <Unlock size={14} />}
                </button>
            </div>

            {/* Bottom Info */}
            <div className="absolute bottom-0 left-0 right-0 p-4 z-30">
                <h3 className="text-lg font-bold text-foreground mb-1 truncate">{asset.name}</h3>
                <p className="text-xs text-foreground/80 line-clamp-2 mb-3 h-8">
                    {asset.description || "No description"}
                </p>

                <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity transform translate-y-2 group-hover:translate-y-0">
                    <WorkflowActionButton
                        onClick={(e) => {
                            e.stopPropagation();
                            onGenerate();
                        }}
                        disabled={isLocked || isGenerating}
                        loading={isGenerating}
                        leftIcon={!isGenerating ? <RefreshCw /> : undefined}
                        variant="primary"
                        size="sm"
                        className="flex-1"
                    >
                        {isGenerating ? "Generating..." : asset.status === "needs_confirmation" ? recovery("newGenerate") : "Generate"}
                    </WorkflowActionButton>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onUpload?.();
                        }}
                        className="px-2.5 rounded-full bg-glass hover:bg-hover-bg border border-glass-border text-foreground cursor-pointer transition-colors"
                        title={tv("uploadAsset")}
                    >
                        <Upload size={14} />
                    </button>
                </div>
            </div>
        </motion.div>
    );
}



function CreateAssetDialog({ type, onClose, onCreate }: { type: string; onClose: () => void; onCreate: (data: { name: string; description: string }) => void }) {
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = async () => {
        if (!name.trim()) {
            alert("Name is required");
            return;
        }
        setIsSubmitting(true);
        try {
            await onCreate({ name: name.trim(), description: description.trim() });
        } finally {
            setIsSubmitting(false);
        }
    };

    const typeLabel = type === "character" ? "Character" : type === "scene" ? "Scene" : "Prop";

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-md overflow-hidden shadow-lg"
            >
                <div className="p-6 border-b border-glass-border flex justify-between items-center bg-surface">
                    <div className="flex items-center gap-3">
                        <Plus className="text-primary" size={20} />
                        <h2 className="text-lg font-bold text-foreground">Create New {typeLabel}</h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-lg transition-colors">
                        <X size={20} className="text-text-secondary" />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-2">Name *</label>
                        <input
                            type="text"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder={`Enter ${type} name`}
                            className="w-full px-4 py-3 bg-input-bg border border-glass-border rounded-lg text-foreground placeholder-text-muted focus:border-primary/50 focus:outline-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-2">Description</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={`Describe the ${type}...`}
                            rows={4}
                            className="w-full px-4 py-3 bg-input-bg border border-glass-border rounded-lg text-foreground placeholder-text-muted focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                </div>

                <div className="p-6 border-t border-glass-border flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-glass hover:bg-hover-bg text-foreground rounded-lg transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting || !name.trim()}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {isSubmitting && <RefreshCw size={16} className="animate-spin" />}
                        Create {typeLabel}
                    </button>
                </div>
            </motion.div>
        </div>
    );
}
