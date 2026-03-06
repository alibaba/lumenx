"use client";

import { useState, useEffect, useRef } from "react";
import { Mic, Play, Pause, Wand2, Users, Volume2, Music, Check, Settings2, Smile, Frown, Angry, Meh } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

export default function VoiceActingStudio() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const [voices, setVoices] = useState<any[]>([]);
    const [playingAudio, setPlayingAudio] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [generatingLineId, setGeneratingLineId] = useState<string | null>(null);

    // Settings state
    const [activeSettingsId, setActiveSettingsId] = useState<string | null>(null);
    const [lineSettings, setLineSettings] = useState<Record<string, { speed: number; pitch: number }>>({});

    useEffect(() => {
        // Fetch available voices
        api.getVoices().then(setVoices).catch(console.error);
    }, []);

    const handlePlay = (url: string) => {
        if (playingAudio === url) {
            audioRef.current?.pause();
            setPlayingAudio(null);
        } else {
            if (audioRef.current) {
                audioRef.current.src = getAssetUrl(url);
                audioRef.current.play();
                setPlayingAudio(url);
            }
        }
    };

    const handleBindVoice = async (charId: string, voiceId: string, voiceName: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.bindVoice(currentProject.id, charId, voiceId, voiceName);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to bind voice:", error);
        }
    };

    const handleGenerateAll = async () => {
        if (!currentProject) return;
        setIsGenerating(true);
        try {
            const updatedProject = await api.generateAudio(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate audio:", error);
        } finally {
            setIsGenerating(false);
        }
    };

    const handleGenerateLine = async (frameId: string) => {
        if (!currentProject) return;
        setGeneratingLineId(frameId);
        try {
            const settings = lineSettings[frameId] || { speed: 1.0, pitch: 1.0 };
            const updatedProject = await api.generateLineAudio(currentProject.id, frameId, settings.speed, settings.pitch);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate line audio:", error);
        } finally {
            setGeneratingLineId(null);
        }
    };

    const insertEmotion = (emotion: string) => {
        // Mock action: In a real app, this would insert into the text editor.
        // For now, we'll just show a visual feedback.
        const activeElement = document.activeElement;
        if (activeElement && activeElement.tagName === 'TEXTAREA') {
            // If we had a textarea, we'd insert here.
        }
        console.log(`[Mock] Inserted emotion: ${emotion}`);
        // Optional: Show a temporary toast or overlay
    };

    const emotions = [
        { label: "Happy", icon: <Smile size={14} />, color: "text-green-400" },
        { label: "Sad", icon: <Frown size={14} />, color: "text-blue-400" },
        { label: "Angry", icon: <Angry size={14} />, color: "text-red-400" },
        { label: "Neutral", icon: <Meh size={14} />, color: "text-gray-400" },
    ];

    return (
        <div className="flex h-full bg-[#0a0a0a] text-white">
            <audio ref={audioRef} onEnded={() => setPlayingAudio(null)} className="hidden" />

            {/* Left Sidebar: Casting Room */}
            <div className="w-80 border-r border-white/10 flex flex-col bg-[#111]">
                <div className="p-4 border-b border-white/10">
                    <h3 className="font-bold text-sm flex items-center gap-2">
                        <Users size={16} className="text-primary" /> Casting Room
                    </h3>
                </div>
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                    {currentProject?.characters?.map((char: any) => (
                        <div key={char.id} className="bg-white/5 rounded-lg p-3 border border-white/5 hover:border-white/10 transition-colors">
                            <div className="flex items-center gap-3 mb-3">
                                <div className="w-10 h-10 rounded-full bg-gray-700 overflow-hidden">
                                    {(char?.avatar_url || char?.image_url) ? (
                                        <img
                                            src={getAssetUrl(char?.avatar_url || char?.image_url)}
                                            alt={char.name}
                                            className="w-full h-full object-cover"
                                        />
                                    ) : (
                                        <div className="w-full h-full flex items-center justify-center text-xs">{char.name[0]}</div>
                                    )}
                                </div>
                                <div>
                                    <div className="font-bold text-sm">{char.name}</div>
                                    <div className="text-xs text-gray-500">{char.gender}, {char.age}</div>
                                </div>
                            </div>

                            {/* Voice Selector */}
                            <div className="space-y-1">
                                <label className="text-[10px] uppercase text-gray-500 font-bold">Assigned Voice</label>
                                <select
                                    className="w-full bg-black/20 border border-white/10 rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-primary"
                                    value={char.voice_id || ""}
                                    onChange={(e) => {
                                        const voice = voices.find(v => v.id === e.target.value);
                                        if (voice) handleBindVoice(char.id, voice.id, voice.name);
                                    }}
                                >
                                    <option value="">Select a voice...</option>
                                    {voices.map(v => (
                                        <option key={v.id} value={v.id}>{v.name}</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {/* Main Content: Script Reader */}
            <div className="flex-1 flex flex-col relative">
                {/* Toolbar */}
                <div className="h-14 border-b border-white/10 bg-[#111] flex items-center px-6 justify-between">
                    <div className="flex items-center gap-4">
                        <h2 className="font-display font-bold text-lg">Script Reader</h2>
                        <div className="h-4 w-px bg-white/10" />

                        {/* Emotion Toolbar */}
                        <div className="flex items-center gap-2">
                            {emotions.map(emo => (
                                <button
                                    key={emo.label}
                                    onClick={() => insertEmotion(emo.label)}
                                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/5 text-xs font-medium transition-colors ${emo.color}`}
                                >
                                    {emo.icon} {emo.label}
                                </button>
                            ))}
                        </div>
                    </div>
                    <button
                        onClick={handleGenerateAll}
                        disabled={isGenerating}
                        className="bg-primary hover:bg-primary/90 text-white px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-2 transition-colors disabled:opacity-50"
                    >
                        {isGenerating ? <Wand2 className="animate-spin" size={16} /> : <Mic size={16} />}
                        {isGenerating ? "Generating..." : "Generate All Audio"}
                    </button>
                </div>

                {/* Dialogue List */}
                <div className="flex-1 overflow-y-auto p-8 space-y-6">
                    {currentProject?.frames?.map((frame: any, index: number) => {
                        if (!frame.dialogue) return null;

                        // Find speaker (simple logic: first char in frame)
                        const speakerId = frame.character_ids?.[0];
                        const speaker = currentProject.characters.find((c: any) => c.id === speakerId);
                        const isSettingsOpen = activeSettingsId === frame.id;
                        const settings = lineSettings[frame.id] || { speed: 1.0, pitch: 1.0 };

                        return (
                            <div key={frame.id} className="flex gap-4 group">
                                {/* Speaker Avatar */}
                                <div className="w-12 flex-shrink-0 flex flex-col items-center gap-1 pt-1">
                                    <div className="w-10 h-10 rounded-full bg-gray-800 overflow-hidden border border-white/10">
                                        {(speaker?.avatar_url || speaker?.image_url) ? (
                                            <img
                                                src={getAssetUrl(speaker?.avatar_url || speaker?.image_url)}
                                                className="w-full h-full object-cover"
                                            />
                                        ) : (
                                            <div className="w-full h-full flex items-center justify-center text-xs text-gray-500">?</div>
                                        )}
                                    </div>
                                    <span className="text-[10px] text-gray-500 text-center leading-tight w-16 truncate">
                                        {speaker?.name || "Unknown"}
                                    </span>
                                </div>

                                {/* Dialogue Bubble */}
                                <div className="flex-1 max-w-3xl">
                                    <div className={`bg-white/5 rounded-2xl rounded-tl-none p-4 border border-white/5 hover:border-white/10 transition-colors relative ${frame.audio_url ? "border-primary/30 bg-primary/5" : ""}`}>

                                        {/* Settings Popover */}
                                        {isSettingsOpen && (
                                            <div className="absolute top-full left-0 mt-2 w-64 bg-[#1a1a1a] border border-white/10 rounded-xl p-4 shadow-xl z-10">
                                                <div className="space-y-4">
                                                    <div>
                                                        <label className="flex justify-between text-xs text-gray-400 mb-1">
                                                            Speed <span>{settings.speed}x</span>
                                                        </label>
                                                        <input
                                                            type="range" min="0.5" max="2.0" step="0.1"
                                                            value={settings.speed}
                                                            onChange={(e) => setLineSettings(prev => ({
                                                                ...prev,
                                                                [frame.id]: { ...settings, speed: parseFloat(e.target.value) }
                                                            }))}
                                                            className="w-full"
                                                        />
                                                    </div>
                                                    <div>
                                                        <label className="flex justify-between text-xs text-gray-400 mb-1">
                                                            Pitch <span>{settings.pitch}</span>
                                                        </label>
                                                        <input
                                                            type="range" min="0.5" max="2.0" step="0.1"
                                                            value={settings.pitch}
                                                            onChange={(e) => setLineSettings(prev => ({
                                                                ...prev,
                                                                [frame.id]: { ...settings, pitch: parseFloat(e.target.value) }
                                                            }))}
                                                            className="w-full"
                                                        />
                                                    </div>
                                                    <button
                                                        onClick={() => {
                                                            handleGenerateLine(frame.id);
                                                            setActiveSettingsId(null);
                                                        }}
                                                        className="w-full bg-primary text-white text-xs py-2 rounded-lg font-bold"
                                                    >
                                                        Regenerate with Settings
                                                    </button>
                                                </div>
                                            </div>
                                        )}

                                        <div className="flex justify-between items-start gap-4">
                                            <p className="text-gray-200 text-lg font-serif leading-relaxed">
                                                &quot;{frame.dialogue}&quot;
                                            </p>

                                            {/* Action Buttons */}
                                            <div className="flex items-center gap-2 flex-shrink-0">
                                                <button
                                                    onClick={() => setActiveSettingsId(isSettingsOpen ? null : frame.id)}
                                                    className={`p-1.5 rounded-full hover:bg-white/10 text-gray-400 transition-colors ${isSettingsOpen ? "bg-white/10 text-white" : ""}`}
                                                >
                                                    <Settings2 size={14} />
                                                </button>

                                                {generatingLineId === frame.id ? (
                                                    <div className="w-8 h-8 rounded-full bg-white/5 flex items-center justify-center">
                                                        <Wand2 className="animate-spin text-primary" size={14} />
                                                    </div>
                                                ) : frame.audio_url ? (
                                                    <button
                                                        onClick={() => handlePlay(frame.audio_url)}
                                                        className={`w-8 h-8 rounded-full flex items-center justify-center transition-colors ${playingAudio === frame.audio_url ? "bg-primary text-white" : "bg-white/10 hover:bg-white/20 text-gray-300"}`}
                                                    >
                                                        {playingAudio === frame.audio_url ? <Pause size={14} /> : <Play size={14} />}
                                                    </button>
                                                ) : (
                                                    <button
                                                        onClick={() => handleGenerateLine(frame.id)}
                                                        className="w-8 h-8 rounded-full bg-white/5 hover:bg-white/10 flex items-center justify-center text-gray-400"
                                                    >
                                                        <Mic size={14} />
                                                    </button>
                                                )}
                                            </div>
                                        </div>

                                        {/* Metadata Footer */}
                                        <div className="mt-3 pt-3 border-t border-white/5 flex items-center justify-between text-xs text-gray-500">
                                            <span className="font-mono">Frame {index + 1}</span>
                                            {frame.audio_url && (
                                                <span className="flex items-center gap-1 text-green-500">
                                                    <Check size={12} /> Audio Ready
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        );
                    })}

                    {(!currentProject?.frames?.some((f: any) => f.dialogue)) && (
                        <div className="text-center text-gray-500 py-20">
                            <Volume2 size={48} className="mx-auto mb-4 opacity-20" />
                            <p>No dialogue found in this script.</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
