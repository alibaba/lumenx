"use client";

import { useState, useEffect, useRef } from "react";
import NextImage from "next/image";
import { Mic, Play, Pause, Wand2, Users, Volume2, Check, Settings2, AlertCircle } from "lucide-react";
import clsx from "clsx";
import { useProjectStore } from "@/store/projectStore";
import type { Character, StoryboardFrame } from "@/store/projectStore";
import { api } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import { messages } from "@/lib/i18n";

const copy = messages.modules.voiceActingStudio;

type VoiceOption = {
    id: string;
    name: string;
    gender?: string;
    model?: string;
};

type VoiceDraft = {
    voiceId: string;
    voiceName: string;
};

type VoiceCharacter = Character & {
    voice_speed?: number;
    voice_pitch?: number;
    voice_volume?: number;
};

type VoiceFrame = StoryboardFrame & {
    audio_error?: string;
};

export default function VoiceActingStudio() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const [voices, setVoices] = useState<VoiceOption[]>([]);
    const [playingAudio, setPlayingAudio] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [generatingLineId, setGeneratingLineId] = useState<string | null>(null);

    // Per-line settings override
    const [activeSettingsId, setActiveSettingsId] = useState<string | null>(null);
    const [lineSettings, setLineSettings] = useState<Record<string, { speed: number; pitch: number; volume: number }>>({});

    // Per-character voice params (defaults)
    const [charParams, setCharParams] = useState<Record<string, { speed: number; pitch: number; volume: number }>>({});
    const [customVoiceDrafts, setCustomVoiceDrafts] = useState<Record<string, VoiceDraft>>({});

    useEffect(() => {
        api.getVoices().then(setVoices).catch(console.error);
    }, []);

    useEffect(() => {
        if (currentProject?.characters) {
            const params: Record<string, { speed: number; pitch: number; volume: number }> = {};
            const drafts: Record<string, VoiceDraft> = {};
            (currentProject.characters as VoiceCharacter[]).forEach((char) => {
                params[char.id] = {
                    speed: char.voice_speed ?? 1.0,
                    pitch: char.voice_pitch ?? 1.0,
                    volume: char.voice_volume ?? 50,
                };
                drafts[char.id] = {
                    voiceId: char.voice_id ?? "",
                    voiceName: char.voice_name ?? "",
                };
            });
            setCharParams(params);
            setCustomVoiceDrafts(drafts);
        }
    }, [currentProject?.characters]);

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
            alert(copy.failedToBindVoice);
        }
    };

    const updateCustomVoiceDraft = (charId: string, key: keyof VoiceDraft, value: string) => {
        setCustomVoiceDrafts((prev) => ({
            ...prev,
            [charId]: {
                voiceId: prev[charId]?.voiceId ?? "",
                voiceName: prev[charId]?.voiceName ?? "",
                [key]: value,
            },
        }));
    };

    const handleBindCustomVoice = async (charId: string) => {
        const draft = customVoiceDrafts[charId];
        const voiceId = draft?.voiceId?.trim();
        const voiceName = draft?.voiceName?.trim() || voiceId;
        if (!voiceId) {
            alert(copy.customVoiceRequired);
            return;
        }
        await handleBindVoice(charId, voiceId, voiceName);
    };

    const handleCharParamChange = (charId: string, param: string, value: number) => {
        setCharParams(prev => ({
            ...prev,
            [charId]: { ...prev[charId], [param]: value }
        }));
    };

    const saveCharParams = async (charId: string) => {
        const params = charParams[charId];
        if (!currentProject || !params) return;
        try {
            const updated = await api.updateVoiceParams(currentProject.id, charId, params.speed, params.pitch, params.volume);
            updateProject(currentProject.id, updated);
        } catch (error) {
            console.error("Failed to save voice params:", error);
            alert(copy.failedToSaveVoiceParams);
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
            alert(copy.failedToGenerateAudio);
        } finally {
            setIsGenerating(false);
        }
    };

    const handleGenerateLine = async (frameId: string) => {
        if (!currentProject) return;
        setGeneratingLineId(frameId);
        try {
            const settings = lineSettings[frameId] || { speed: 1.0, pitch: 1.0, volume: 50 };
            const updatedProject = await api.generateLineAudio(currentProject.id, frameId, settings.speed, settings.pitch, settings.volume);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate line audio:", error);
            alert(copy.failedToGenerateLineAudio);
        } finally {
            setGeneratingLineId(null);
        }
    };

    const voiceCharacters = (currentProject?.characters ?? []) as VoiceCharacter[];
    const dialogueFrames = (currentProject?.frames ?? []) as VoiceFrame[];

    return (
        <div className="flex h-full text-white">
            <audio ref={audioRef} onEnded={() => setPlayingAudio(null)} className="hidden" />

            {/* Left Sidebar: Casting Room */}
            <div className="w-80 border-r border-white/10 flex flex-col bg-black/20">
                <div className="p-4 border-b border-white/10">
                    <h3 className="font-display font-bold text-sm flex items-center gap-2">
                        <Users size={16} className="text-primary" /> {copy.castingRoom}
                    </h3>
                </div>
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                    {voiceCharacters.map((char) => (
                        <div key={char.id} className="bg-white/5 rounded-lg p-3 border border-white/5 hover:border-white/10 transition-colors">
                            <div className="flex items-center gap-3 mb-3">
                                <div className="w-10 h-10 rounded-full bg-gray-700 overflow-hidden">
                                    {(char?.avatar_url || char?.image_url) ? (
                                        <NextImage
                                            src={getAssetUrl(char?.avatar_url || char?.image_url)}
                                            alt={char.name}
                                            width={40}
                                            height={40}
                                            unoptimized
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
                                <label className="text-[10px] uppercase text-gray-500 font-bold">{copy.assignedVoice}</label>
                                {(() => {
                                    const presetVoice = voices.find((voice) => voice.id === char.voice_id);
                                    const selectValue = char.voice_id ? (presetVoice ? char.voice_id : "__custom__") : "";
                                    const draft = customVoiceDrafts[char.id] ?? { voiceId: "", voiceName: "" };

                                    return (
                                        <>
                                <select
                                    className="w-full bg-black/20 border border-white/10 rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-primary"
                                    value={selectValue}
                                    onChange={(e) => {
                                        if (e.target.value === "__custom__") {
                                            return;
                                        }
                                        const voice = voices.find(v => v.id === e.target.value);
                                        if (voice) {
                                            updateCustomVoiceDraft(char.id, "voiceId", voice.id);
                                            updateCustomVoiceDraft(char.id, "voiceName", voice.name);
                                            handleBindVoice(char.id, voice.id, voice.name);
                                        }
                                    }}
                                >
                                    <option value="">{copy.selectVoice}</option>
                                    {voices.map(v => (
                                        <option key={v.id} value={v.id}>{v.name}</option>
                                    ))}
                                    <option value="__custom__">{copy.customVoiceOption}</option>
                                </select>
                                            <p className="text-[10px] text-gray-500 mt-2">{copy.customVoiceHint}</p>
                                            <input
                                                type="text"
                                                value={draft.voiceId}
                                                onChange={(e) => updateCustomVoiceDraft(char.id, "voiceId", e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter") {
                                                        void handleBindCustomVoice(char.id);
                                                    }
                                                }}
                                                placeholder={copy.customVoiceIdPlaceholder}
                                                className="mt-2 w-full bg-black/20 border border-white/10 rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-primary"
                                            />
                                            <input
                                                type="text"
                                                value={draft.voiceName}
                                                onChange={(e) => updateCustomVoiceDraft(char.id, "voiceName", e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter") {
                                                        void handleBindCustomVoice(char.id);
                                                    }
                                                }}
                                                placeholder={copy.customVoiceNamePlaceholder}
                                                className="mt-2 w-full bg-black/20 border border-white/10 rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-primary"
                                            />
                                            <button
                                                type="button"
                                                onClick={() => void handleBindCustomVoice(char.id)}
                                                className="mt-2 w-full bg-white/5 hover:bg-white/10 border border-white/10 hover:border-primary/40 text-xs text-gray-200 py-2 rounded-lg transition-colors"
                                            >
                                                {copy.applyCustomVoice}
                                            </button>
                                        </>
                                    );
                                })()}
                            </div>

                            {/* Per-Character Voice Params */}
                            <div className="mt-3 space-y-2">
                                <div>
                                    <label className="flex justify-between text-[10px] text-gray-500 mb-0.5">
                                        {copy.speed} <span>{(charParams[char.id]?.speed ?? 1.0).toFixed(1)}x</span>
                                    </label>
                                    <input type="range" min="0.5" max="2.0" step="0.1"
                                        value={charParams[char.id]?.speed ?? 1.0}
                                        onChange={(e) => handleCharParamChange(char.id, 'speed', parseFloat(e.target.value))}
                                        onPointerUp={() => saveCharParams(char.id)}
                                        className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary"
                                    />
                                </div>
                                <div>
                                    <label className="flex justify-between text-[10px] text-gray-500 mb-0.5">
                                        {copy.pitch} <span>{(charParams[char.id]?.pitch ?? 1.0).toFixed(1)}</span>
                                    </label>
                                    <input type="range" min="0.5" max="2.0" step="0.1"
                                        value={charParams[char.id]?.pitch ?? 1.0}
                                        onChange={(e) => handleCharParamChange(char.id, 'pitch', parseFloat(e.target.value))}
                                        onPointerUp={() => saveCharParams(char.id)}
                                        className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary"
                                    />
                                </div>
                                <div>
                                    <label className="flex justify-between text-[10px] text-gray-500 mb-0.5">
                                        {copy.volume} <span>{charParams[char.id]?.volume ?? 50}</span>
                                    </label>
                                    <input type="range" min="0" max="100" step="1"
                                        value={charParams[char.id]?.volume ?? 50}
                                        onChange={(e) => handleCharParamChange(char.id, 'volume', parseInt(e.target.value))}
                                        onPointerUp={() => saveCharParams(char.id)}
                                        className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary"
                                    />
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {/* Main Content: Script Reader */}
            <div className="flex-1 flex flex-col relative">
                {/* Toolbar */}
                <div className="h-14 border-b border-white/10 bg-black/20 flex items-center px-6 justify-between">
                    <h2 className="font-display font-bold text-lg">{copy.scriptReader}</h2>
                    <button
                        onClick={handleGenerateAll}
                        disabled={isGenerating}
                        className="bg-white/5 hover:bg-white/10 border border-primary/50 hover:border-primary text-primary hover:text-white px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-2 whitespace-nowrap flex-shrink-0 transition-all disabled:opacity-50"
                    >
                        {isGenerating ? <Wand2 className="animate-spin" size={16} /> : <Mic size={16} />}
                        {isGenerating ? copy.generating : copy.generateAllAudio}
                    </button>
                </div>

                {/* Dialogue List */}
                <div className="flex-1 overflow-y-auto p-8 space-y-6">
                    {dialogueFrames.map((frame, index) => {
                        if (!frame.dialogue) return null;

                        const speakerId = frame.character_ids?.[0];
                        const speaker = voiceCharacters.find((character) => character.id === speakerId);
                        const audioUrl = frame.audio_url;
                        const isSettingsOpen = activeSettingsId === frame.id;
                        const settings = lineSettings[frame.id] || { speed: 1.0, pitch: 1.0, volume: 50 };

                        return (
                            <div key={frame.id} className="flex gap-4 group">
                                {/* Speaker Avatar */}
                                <div className="w-12 flex-shrink-0 flex flex-col items-center gap-1 pt-1">
                                    <div className="w-10 h-10 rounded-full bg-gray-800 overflow-hidden border border-white/10">
                                        {(speaker?.avatar_url || speaker?.image_url) ? (
                                            <NextImage
                                                src={getAssetUrl(speaker?.avatar_url || speaker?.image_url)}
                                                alt={speaker?.name || copy.unknown}
                                                width={40}
                                                height={40}
                                                unoptimized
                                                className="w-full h-full object-cover"
                                            />
                                        ) : (
                                            <div className="w-full h-full flex items-center justify-center text-xs text-gray-500">?</div>
                                        )}
                                    </div>
                                    <span className="text-[10px] text-gray-500 text-center leading-tight w-16 truncate">
                                        {speaker?.name || copy.unknown}
                                    </span>
                                </div>

                                {/* Dialogue Bubble */}
                                <div className="flex-1 max-w-3xl">
                                    <div className={clsx(
                                        "bg-white/5 rounded-2xl rounded-tl-none p-4 border border-white/5 hover:border-white/10 transition-colors relative",
                                        frame.audio_url && "border-primary/30 bg-primary/5"
                                    )}>

                                        {/* Settings Popover */}
                                        {isSettingsOpen && (
                                            <div className="absolute top-full left-0 mt-2 w-64 bg-black/30 backdrop-blur-xl border border-white/10 rounded-xl p-4 shadow-xl z-10">
                                                <div className="space-y-4">
                                                    <div>
                                                        <label className="flex justify-between text-xs text-gray-400 mb-1">
                                                            {copy.speed} <span>{settings.speed}x</span>
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
                                                            {copy.pitch} <span>{settings.pitch}</span>
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
                                                    <div>
                                                        <label className="flex justify-between text-xs text-gray-400 mb-1">
                                                            {copy.volume} <span>{settings.volume}</span>
                                                        </label>
                                                        <input
                                                            type="range" min="0" max="100" step="1"
                                                            value={settings.volume}
                                                            onChange={(e) => setLineSettings(prev => ({
                                                                ...prev,
                                                                [frame.id]: { ...settings, volume: parseInt(e.target.value) }
                                                            }))}
                                                            className="w-full"
                                                        />
                                                    </div>
                                                    <button
                                                        onClick={() => {
                                                            handleGenerateLine(frame.id);
                                                            setActiveSettingsId(null);
                                                        }}
                                                        className="w-full bg-white/5 hover:bg-white/10 border border-primary/50 hover:border-primary text-primary hover:text-white text-xs py-2 rounded-lg font-bold transition-all"
                                                    >
                                                        {copy.regenerateWithSettings}
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
                                                    className={clsx(
                                                        "p-1.5 rounded-full hover:bg-white/10 text-gray-400 transition-colors",
                                                        isSettingsOpen && "bg-white/10 text-white"
                                                    )}
                                                >
                                                    <Settings2 size={14} />
                                                </button>

                                                {generatingLineId === frame.id ? (
                                                    <div className="w-8 h-8 rounded-full bg-white/5 flex items-center justify-center">
                                                        <Wand2 className="animate-spin text-primary" size={14} />
                                                    </div>
                                                ) : audioUrl ? (
                                                    <button
                                                        onClick={() => handlePlay(audioUrl)}
                                                        className={clsx(
                                                            "w-8 h-8 rounded-full flex items-center justify-center transition-colors",
                                                            playingAudio === audioUrl ? "bg-primary text-white" : "bg-white/10 hover:bg-white/20 text-gray-300"
                                                        )}
                                                    >
                                                        {playingAudio === audioUrl ? <Pause size={14} /> : <Play size={14} />}
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
                                            <span className="font-mono">{copy.frameNumber(index + 1)}</span>
                                            {frame.status === "failed" ? (
                                                <span className="flex items-center gap-1 text-red-400" title={frame.audio_error || copy.generationFailed}>
                                                    <AlertCircle size={12} /> {frame.audio_error || copy.audioGenerationFailed}
                                                </span>
                                            ) : frame.audio_url ? (
                                                <span className="flex items-center gap-1 text-green-500">
                                                    <Check size={12} /> {copy.audioReady}
                                                </span>
                                            ) : null}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        );
                    })}

                    {(!dialogueFrames.some((frame) => frame.dialogue)) && (
                        <div className="text-center text-gray-500 py-20">
                            <Volume2 size={48} className="mx-auto mb-4 opacity-20" />
                            <p>{copy.noDialogue}</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
