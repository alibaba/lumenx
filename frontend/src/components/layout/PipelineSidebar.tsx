"use client";

import { motion } from "framer-motion";
import {
    ChevronRight,
    ChevronLeft
} from "lucide-react";
import clsx from "clsx";
import { messages } from "@/lib/i18n";
import LumenXBranding from "./LumenXBranding";
import type { BreadcrumbSegment } from "./BreadcrumbBar";

interface Step {
    id: string;
    label: string;
    icon: any;
    comingSoon?: boolean;
}

interface PipelineSidebarProps {
    activeStep: string;
    onStepChange: (stepId: string) => void;
    steps: Step[];
    breadcrumbSegments?: BreadcrumbSegment[];
    headerActions?: React.ReactNode;
}

export default function PipelineSidebar({ activeStep, onStepChange, steps, breadcrumbSegments, headerActions }: PipelineSidebarProps) {
    const handleBack = () => {
        if (!breadcrumbSegments) return;
        if (breadcrumbSegments.length >= 2 && breadcrumbSegments[breadcrumbSegments.length - 2].hash) {
            window.location.hash = breadcrumbSegments[breadcrumbSegments.length - 2].hash!;
        } else if (breadcrumbSegments[0]?.hash) {
            window.location.hash = breadcrumbSegments[0].hash;
        } else {
            window.location.hash = "";
        }
    };

    return (
        <motion.aside
            initial={{ x: -100, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            className="w-64 flex-1 min-h-0 border-r border-glass-border bg-black/40 backdrop-blur-xl flex flex-col z-50"
        >
            {/* Header: breadcrumb navigation or branding */}
            <div className="p-5 border-b border-glass-border">
                {breadcrumbSegments ? (
                    <div className="space-y-3">
                        {/* Breadcrumb row */}
                        <div className="flex items-center gap-1.5">
                            <button
                                onClick={handleBack}
                                className="flex-shrink-0 text-gray-400 hover:text-white transition-colors"
                                title={messages.navigation.back}
                            >
                                <ChevronLeft size={16} />
                            </button>
                            <nav className="flex items-center gap-1 text-xs min-w-0 flex-1">
                                {breadcrumbSegments.map((seg, i) => {
                                    const isLast = i === breadcrumbSegments.length - 1;
                                    return (
                                        <span key={i} className="flex items-center gap-1 min-w-0">
                                            {i > 0 && <span className="text-gray-600 flex-shrink-0">&rsaquo;</span>}
                                            {seg.hash && !isLast ? (
                                                <a
                                                    href={seg.hash}
                                                    className="text-gray-500 hover:text-white transition-colors truncate"
                                                >
                                                    {seg.label}
                                                </a>
                                            ) : (
                                                <span className={clsx(
                                                    "truncate",
                                                    isLast ? "text-white font-medium" : "text-gray-500"
                                                )}>
                                                    {seg.label}
                                                </span>
                                            )}
                                        </span>
                                    );
                                })}
                            </nav>
                        </div>
                        {/* Actions row */}
                        {headerActions && (
                            <div className="flex items-center gap-1">
                                {headerActions}
                            </div>
                        )}
                    </div>
                ) : (
                    <LumenXBranding size="sm" />
                )}
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

                            <Icon size={20} className={clsx(
                                "transition-colors",
                                step.comingSoon ? "opacity-50" : "",
                                isActive ? "text-primary" : "group-hover:text-white"
                            )} />

                            <div className="flex flex-col items-start text-sm flex-1">
                                <div className="flex items-center gap-2">
                                    <span className={clsx("font-medium", step.comingSoon && "opacity-70")}>{step.label}</span>
                                    {step.comingSoon && (
                                        <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30 font-medium">
                                            Beta
                                        </span>
                                    )}
                                </div>
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
                        <span className="text-sm font-medium text-white">{messages.navigation.pipeline}</span>
                        <span className="text-xs text-gray-500">v0.1.0</span>
                    </div>
                </div>
            </div>
        </motion.aside>
    );
}
