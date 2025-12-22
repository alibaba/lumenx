"use client";

import { motion } from "framer-motion";
import {
    FileText,
    LayoutGrid,
    Palette,
    Film,
    Music,
    Download,
    ChevronRight
} from "lucide-react";
import clsx from "clsx";

import { LucideIcon } from "lucide-react";

interface Step {
    id: string;
    label: string;
    icon: any; // Using any for LucideIcon to avoid type strictness issues with different versions
}

interface PipelineSidebarProps {
    activeStep: string;
    onStepChange: (stepId: string) => void;
    steps: Step[];
}

export default function PipelineSidebar({ activeStep, onStepChange, steps }: PipelineSidebarProps) {
    return (
        <motion.aside
            initial={{ x: -100, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            className="w-64 h-screen border-r border-glass-border bg-black/40 backdrop-blur-xl flex flex-col z-50"
        >
            <div className="p-5 border-b border-glass-border">
                <div className="flex gap-4 items-center">
                    {/* Left Column: Large Logo */}
                    <div className="flex-shrink-0">
                        <img
                            src="/static/LumenX.png"
                            alt="LumenX"
                            className="w-16 h-16 object-contain"
                        />
                    </div>

                    {/* Right Column: LumenX / Studio */}
                    <div className="flex flex-col flex-1 justify-center h-full gap-1">
                        {/* LumenX (Top Left) */}
                        <div className="flex items-center justify-start -mb-1">
                            <span className="font-display text-3xl font-bold tracking-tight text-primary">
                                Lumen
                            </span>
                            <span
                                className="font-display text-4xl font-black tracking-tighter ml-1"
                                style={{
                                    background: 'linear-gradient(135deg, #a855f7 0%, #6366f1 50%, #ec4899 100%)',
                                    WebkitBackgroundClip: 'text',
                                    WebkitTextFillColor: 'transparent',
                                    backgroundClip: 'text',
                                }}
                            >
                                X
                            </span>
                        </div>

                        {/* Studio (Bottom Right) */}
                        <div className="flex justify-end -mt-1 pr-2">
                            <span className="font-display text-3xl font-bold tracking-tight text-white">
                                Studio
                            </span>
                        </div>
                    </div>
                </div>

                {/* Slogan */}
                <p className="text-[9px] text-gray-500 tracking-wide text-center mt-3">
                    Render Noise into Narrative
                </p>
            </div>

            <nav className="flex-1 p-4 space-y-2 overflow-y-auto">
                {steps.map((step, index) => {
                    const isActive = activeStep === step.id;
                    const Icon = step.icon;

                    return (
                        <button
                            key={step.id}
                            onClick={() => onStepChange(step.id)}
                            className={clsx(
                                "w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200 group relative overflow-hidden",
                                isActive
                                    ? "bg-primary/10 text-primary border border-primary/20"
                                    : "text-gray-400 hover:text-white hover:bg-white/5"
                            )}
                        >
                            {isActive && (
                                <motion.div
                                    layoutId="active-pill"
                                    className="absolute left-0 w-1 h-full bg-primary"
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                />
                            )}

                            <Icon size={20} className={clsx("transition-colors", isActive ? "text-primary" : "group-hover:text-white")} />

                            <div className="flex flex-col items-start text-sm">
                                <span className="font-medium">{step.label}</span>
                                <span className="text-[10px] opacity-50 font-mono">STEP 0{index + 1}</span>
                            </div>

                            {isActive && (
                                <ChevronRight size={16} className="ml-auto opacity-50" />
                            )}
                        </button>
                    );
                })}
            </nav>

            <div className="p-4 border-t border-glass-border">
                <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-white/5 border border-white/5">
                    <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-primary to-accent" />
                    <div className="flex flex-col">
                        <span className="text-sm font-medium text-white">Project Alpha</span>
                        <span className="text-xs text-gray-500">v0.1.0</span>
                    </div>
                </div>
            </div>
        </motion.aside>
    );
}
