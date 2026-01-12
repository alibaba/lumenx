"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronLeft, FileText, Palette, Layout, Film, Share2, Mic, Music, BookOpen, Users, Video, ArrowLeft, Settings, Key } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import PipelineSidebar from "@/components/layout/PipelineSidebar";
import PropertiesPanel from "@/components/modules/PropertiesPanel";
import ScriptProcessor from "@/components/modules/ScriptProcessor";
import AssetGrid from "@/components/modules/AssetGrid";
import Timeline from "@/components/modules/Timeline";
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
import dynamic from "next/dynamic";

import { useRouter } from "next/navigation";

const CreativeCanvas = dynamic(() => import("@/components/canvas/CreativeCanvas"), { ssr: false });

export default function ProjectClient({ params }: { params: { id: string } }) {
    const router = useRouter();
    const [activeStep, setActiveStep] = useState("script");
    const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
    const [envDialogOpen, setEnvDialogOpen] = useState(false);

    const selectProject = useProjectStore((state) => state.selectProject);
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const handleBackToHome = () => {
        // 使用 Next.js 路由返回主页
        router.push('/');
    };

    const steps = [
        { id: "script", label: "1. Script", icon: BookOpen },
        { id: "art_direction", label: "2. Art Direction", icon: Palette },
        { id: "assets", label: "3. Assets", icon: Users },
        { id: "storyboard", label: "4. Storyboard", icon: Layout },
        { id: "motion", label: "5. Motion", icon: Video },
        { id: "assembly", label: "6. Assembly", icon: Film },
        { id: "audio", label: "7. Voice", icon: Mic, comingSoon: true },
        { id: "mix", label: "8. Final Mix", icon: Music, comingSoon: true },
        { id: "export", label: "9. Export", icon: Share2, comingSoon: true },
    ];

    useEffect(() => {
        selectProject(params.id);
    }, [params.id, selectProject]);

    if (!currentProject) {
        return (
            <div className="flex items-center justify-center h-screen bg-background">
                <div className="text-center">
                    <p className="text-gray-400 mb-4">项目未找到</p>
                    <button
                        onClick={handleBackToHome}
                        className="text-primary hover:underline"
                    >
                        返回项目列表
                    </button>
                </div>
            </div>
        );
    }

    return (
        <main className="flex h-screen w-screen bg-background overflow-hidden relative">
            {/* Background Canvas */}
            <div className="absolute inset-0 z-0 pointer-events-auto">
                <CreativeCanvas />
            </div>

            {/* Left Sidebar */}
            <div className="relative z-20 h-full flex flex-col">
                {/* Back Button & Settings */}
                <div className="p-4 border-b border-glass-border bg-black/40 backdrop-blur-xl flex justify-between items-center">
                    <button
                        onClick={handleBackToHome}
                        className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors text-sm"
                    >
                        <ArrowLeft size={16} />
                        返回项目列表
                    </button>

                    <div className="flex gap-1">
                        <button
                            onClick={() => setEnvDialogOpen(true)}
                            className="p-2 hover:bg-white/10 rounded-lg transition-colors group"
                            title="API Key & OSS 配置"
                        >
                            <Key size={18} className="text-gray-400 group-hover:text-green-400 transition-colors" />
                        </button>
                        <button
                            onClick={() => setModelSettingsOpen(true)}
                            className="p-2 hover:bg-white/10 rounded-lg transition-colors group"
                            title="Model Settings"
                        >
                            <Settings size={18} className="text-gray-400 group-hover:text-white transition-colors" />
                        </button>
                    </div>
                </div>

                <PipelineSidebar
                    activeStep={activeStep}
                    onStepChange={setActiveStep}
                    steps={steps}
                />
            </div>

            {/* Model Settings Modal */}
            <ModelSettingsModal
                isOpen={modelSettingsOpen}
                onClose={() => setModelSettingsOpen(false)}
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
