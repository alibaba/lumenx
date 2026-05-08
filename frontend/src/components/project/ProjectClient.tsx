"use client";

import { useEffect, useState } from "react";
import { Palette, Layout, Film, Share2, Mic, Music, BookOpen, Users, Video, Settings, Key, MessageSquareCode } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import PipelineSidebar from "@/components/layout/PipelineSidebar";
import type { BreadcrumbSegment } from "@/components/layout/BreadcrumbBar";
import PropertiesPanel from "@/components/modules/PropertiesPanel";
import ScriptProcessor from "@/components/modules/ScriptProcessor";
import VideoGenerator from "@/components/modules/VideoGenerator";
import VideoAssembly from "@/components/modules/VideoAssembly";
import ConsistencyVault from "@/components/modules/ConsistencyVault";
import ArtDirection from "@/components/modules/ArtDirection";
import StoryboardComposer from "@/components/modules/StoryboardComposer";
import VoiceActingStudio from "@/components/modules/VoiceActingStudio";
import FinalMixStudio from "@/components/modules/FinalMixStudio";
import ExportStudio from "@/components/modules/ExportStudio";
import ModelSettingsModal from "@/components/common/ModelSettingsModal";
import EnvConfigDialog from "@/components/project/EnvConfigDialog";
import PromptConfigModal from "@/components/project/PromptConfigModal";
import dynamic from "next/dynamic";
import { messages } from "@/lib/i18n";

const CreativeCanvas = dynamic(() => import("@/components/canvas/CreativeCanvas"), { ssr: false });

export default function ProjectClient({ id, breadcrumbSegments }: { id: string; breadcrumbSegments?: BreadcrumbSegment[] }) {
    const copy = messages.pipeline;
    const projectCopy = messages.homePage.projectClient;
    const homeCopy = messages.homePage;
    const [activeStep, setActiveStep] = useState("script");
    const [isResolvingProject, setIsResolvingProject] = useState(true);
    const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
    const [envDialogOpen, setEnvDialogOpen] = useState(false);
    const [promptConfigOpen, setPromptConfigOpen] = useState(false);

    const selectProject = useProjectStore((state) => state.selectProject);
    const currentProject = useProjectStore((state) => state.currentProject);
    const hasMatchedProject = currentProject?.id === id;

    const handleBackToHome = () => {
        window.location.hash = '';
    };

    const steps = [
        { id: "script", label: copy.steps.script, icon: BookOpen },
        { id: "art_direction", label: copy.steps.artDirection, icon: Palette },
        { id: "assets", label: copy.steps.assets, icon: Users },
        { id: "storyboard", label: copy.steps.storyboard, icon: Layout },
        { id: "motion", label: copy.steps.motion, icon: Video },
        { id: "assembly", label: copy.steps.assembly, icon: Film },
        { id: "audio", label: copy.steps.voice, icon: Mic, comingSoon: true },
        { id: "mix", label: copy.steps.finalMix, icon: Music, comingSoon: true },
        { id: "export", label: copy.steps.export, icon: Share2, comingSoon: true },
    ];

    useEffect(() => {
        let cancelled = false;
        setIsResolvingProject(true);
        Promise.resolve(selectProject(id)).finally(() => {
            if (!cancelled) {
                setIsResolvingProject(false);
            }
        });
        return () => {
            cancelled = true;
        };
    }, [id, selectProject]);

    if (isResolvingProject && !hasMatchedProject) {
        return (
            <div className="flex items-center justify-center h-screen bg-background">
                <div className="text-center">
                    <p className="text-gray-400">{projectCopy.loadingProject}</p>
                </div>
            </div>
        );
    }

    if (!hasMatchedProject) {
        return (
            <div className="flex items-center justify-center h-screen bg-background">
                <div className="text-center">
                    <p className="text-gray-400 mb-4">{projectCopy.projectNotFound}</p>
                    <button
                        onClick={handleBackToHome}
                        className="text-primary hover:underline"
                    >
                        {projectCopy.backToProjectList}
                    </button>
                </div>
            </div>
        );
    }

    const segments = breadcrumbSegments || [{ label: homeCopy.breadcrumb.root, hash: "#/" }, { label: currentProject.title }];

    const settingsActions = (
        <>
            <button
                onClick={() => setEnvDialogOpen(true)}
                className="p-2 hover:bg-white/10 rounded-lg transition-colors group"
                title={copy.actions.apiAndOssConfig}
            >
                <Key size={16} className="text-gray-400 group-hover:text-green-400 transition-colors" />
            </button>
            <button
                onClick={() => setPromptConfigOpen(true)}
                className="p-2 hover:bg-white/10 rounded-lg transition-colors group"
                title={copy.actions.promptConfiguration}
            >
                <MessageSquareCode size={16} className="text-gray-400 group-hover:text-purple-400 transition-colors" />
            </button>
            <button
                onClick={() => setModelSettingsOpen(true)}
                className="p-2 hover:bg-white/10 rounded-lg transition-colors group"
                title={copy.actions.modelSettings}
            >
                <Settings size={16} className="text-gray-400 group-hover:text-white transition-colors" />
            </button>
        </>
    );

    return (
        <main className="flex h-screen w-screen bg-background overflow-hidden relative" data-testid="project-client">
            {/* Background Canvas */}
            <div className="absolute inset-0 z-0 pointer-events-auto">
                <CreativeCanvas />
            </div>

            {/* Left Sidebar — unified PipelineSidebar with integrated breadcrumb */}
            <div className="relative z-20 h-full flex flex-col overflow-hidden">
                <PipelineSidebar
                    activeStep={activeStep}
                    onStepChange={setActiveStep}
                    steps={steps}
                    breadcrumbSegments={segments}
                    headerActions={settingsActions}
                />
            </div>

            {/* Model Settings Modal */}
            <ModelSettingsModal
                isOpen={modelSettingsOpen}
                onClose={() => setModelSettingsOpen(false)}
            />

            {/* Prompt Config Modal */}
            <PromptConfigModal
                isOpen={promptConfigOpen}
                onClose={() => setPromptConfigOpen(false)}
            />

            {/* Environment Config Dialog */}
            <EnvConfigDialog
                isOpen={envDialogOpen}
                onClose={() => setEnvDialogOpen(false)}
                isRequired={false}
            />

            {/* Main Content Area */}
            <div className="flex-1 flex overflow-hidden relative z-10">
                <div className="flex-1 overflow-hidden relative">
                    {activeStep === "script" && <ScriptProcessor />}
                    {activeStep === "art_direction" && <ArtDirection />}
                    {activeStep === "assets" && <ConsistencyVault />}
                    {activeStep === "storyboard" && <StoryboardComposer />}
                    {activeStep === "motion" && <VideoGenerator />}
                    {activeStep === "assembly" && <VideoAssembly />}
                    {activeStep === "audio" && <VoiceActingStudio />}
                    {activeStep === "mix" && <FinalMixStudio />}
                    {activeStep === "export" && <ExportStudio />}
                </div>

                {/* Right Sidebar - Contextual Inspector */}
                {activeStep !== "assembly" && activeStep !== "art_direction" && <PropertiesPanel activeStep={activeStep} />}
            </div>
        </main>
    );
}
