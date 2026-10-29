"use client";

import { useState, useEffect, useRef, useMemo, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { X, RefreshCw, Check, AlertTriangle, Image as ImageIcon, Lock, Unlock, ChevronRight, Maximize2, Video } from "lucide-react";
import AssetRecoveryNotice from "@/components/shared/AssetRecoveryNotice";
import StudioImageRequestResults from "@/components/shared/StudioImageRequestResults";
import StudioMotionRequestResults from "@/components/shared/StudioMotionRequestResults";
import { useStudioMotionRequests } from "@/lib/useStudioMotionRequests";
import { readStudioMotionEligibility } from "@/lib/studioMotionRequest";
import { isStudioFullBodyEditRequest, isStudioGptImageModel, isStudioWanImageModel, isStudioQwenImageModel, listStudioAssetImageRequests, type StudioDerivedImageReferenceSource } from "@/lib/studioAssetImageRequest";
import { serializeJournalData } from "@/lib/studioRequestJournal";
import { sameReadVersion } from "@/lib/frameCommandSnapshot";
import { isStudioFullBodyEligible, usesStudioImageCore, usesStudioQwenImageCore, usesStudioGptThreeView, usesStudioGptHeadshot, useStudioAssetImageRequests } from "@/lib/useStudioAssetImageRequests";
import { isAssetGenerationActive } from "@/lib/assetGenerationOwnership";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { api, API_URL } from "@/lib/api";

import { VariantSelector } from "../common/VariantSelector";
import { VideoVariantSelector } from "../common/VideoVariantSelector";
import { useProjectStore, type Character, type Project } from "@/store/projectStore";
import { Image as PhotoIcon } from "lucide-react";
import { getAssetUrl } from "@/lib/utils";
import { confirmProjectAck, ProjectReadbackError, readProjectOnce } from "@/lib/projectReadback";


/** Use the saved choice and raw reference; the backend validates managed-root ownership. */
function selectedImageEditReference(character: Pick<Character, 'full_body_asset'>): { id: string; url: string } | null {
    const unit = character.full_body_asset;
    if (!unit?.selected_id || !Array.isArray(unit.variants)) return null;
    const matches = unit.variants.filter(variant => variant.id === unit.selected_id);
    if (matches.length !== 1) return null;
    const variant = matches[0], refs = 'media_refs' in variant ? variant.media_refs : null;
    const raw = refs && typeof refs === 'object' && Object.hasOwn(refs, 'url') && 'url' in refs ? refs.url : variant.url;
    if (typeof raw !== 'string' || !raw || raw.trim() !== raw || /[\x00-\x1f\x7f]/.test(raw)
        || raw.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^[a-z]:[\\/]/i.test(raw)) return null;
    return { id: variant.id, url: raw };
}

type DerivedImageReference = { id?: string; url: string; source?: StudioDerivedImageReferenceSource };
type DerivedImageFallback = { kind: 'none' | 'invalid' | 'legacy' } | { kind: 'local'; reference: DerivedImageReference };
type DerivedReferenceCharacter = Pick<Character, 'full_body_asset' | 'full_body_image_url' | 'headshot_asset' | 'three_view_asset' | 'base_character_id'>;

/** Preserve raw source presence and first-upload priority independently for each channel. */
function derivedImageFallback(character: DerivedReferenceCharacter, channel: 'three_view' | 'headshot'): DerivedImageFallback {
    if (character.full_body_asset?.selected_id) return { kind: 'none' };
    const raw = (value: object, field: string): unknown => {
        const record = value as Record<string, unknown>, refs = record.media_refs;
        return refs && typeof refs === 'object' && Object.hasOwn(refs, field)
            ? (refs as Record<string, unknown>)[field] : record[field];
    };
    let url = raw(character, 'full_body_image_url');
    let source: StudioDerivedImageReferenceSource = 'full_body_image_url', id: string | undefined;
    if (!url) {
        const channels = channel === 'three_view' ? ['headshot', 'three_view'] as const : ['three_view', 'headshot'] as const;
        for (const name of channels) {
            const images = name === 'headshot' ? character.headshot_asset : character.three_view_asset;
            if (!Array.isArray(images?.variants)) continue;
            const variant = images.variants.find(value => value.is_uploaded_source);
            if (!variant) continue;
            if (typeof variant.id !== 'string' || !variant.id || variant.id.trim() !== variant.id || /[\x00-\x1f\x7f]/.test(variant.id)
                || images.variants.filter(value => value.id === variant.id).length !== 1) return { kind: 'invalid' };
            source = name === 'headshot' ? 'headshot_upload' : 'three_view_upload'; id = variant.id;
            url = raw(variant, 'url');
            // An explicit invalid raw first upload cannot become its display URL or a later upload.
            if (!url) return { kind: 'invalid' };
            break;
        }
    }
    if (!url) return { kind: 'none' };
    if (typeof url !== 'string' || url.trim() !== url || /[\x00-\x1f\x7f]/.test(url) || url.startsWith('//')) return { kind: 'invalid' };
    if (/^https?:\/\//.test(url)) return { kind: 'legacy' };
    const drive = /^[a-z]:[\\/]/i.test(url);
    if (/^[a-z][a-z0-9+.-]*:/i.test(url) && !drive || url.split(/[\\/]/).includes('..')
        || url.includes('\\') && !drive) return { kind: 'invalid' };
    if (url.startsWith('/') || drive || /^(?:assets|storyboard|video|audio|export|uploads|playground|outputs?)\//.test(url)) {
        // The server remains the authority for managed-root and immutable-byte validation.
        return { kind: 'local', reference: { source, url, ...(id === undefined ? {} : { id }) } };
    }
    return { kind: 'legacy' }; // Remote/object ownership remains on its existing route.
}

function localDerivedImageReference(character: DerivedReferenceCharacter, channel: 'three_view' | 'headshot'): DerivedImageReference | null {
    if (character.full_body_asset?.selected_id) return selectedImageEditReference(character);
    const fallback = derivedImageFallback(character, channel);
    return fallback.kind === 'local' ? fallback.reference : null;
}

type FullBodyEditSource =
    | { kind: 'none' | 'invalid'; channel?: 'three_view' | 'headshot'; id?: never; url?: never }
    | { kind: 'local' | 'legacy'; channel?: 'three_view' | 'headshot'; id: string; url: string };

// Parent references use the author's selected raw full-body image; uploads keep their original priority.
function fullBodyEditSource(character: Pick<Character, 'three_view_asset' | 'headshot_asset' | 'base_character_id' | 'source' | 'id'>, project?: Pick<Project, 'characters'> | null): FullBodyEditSource {
    if (character.base_character_id) {
        if (character.source && character.source !== 'episode') return { kind: 'none' };
        const parents = project?.characters.filter(item => item.id === character.base_character_id && item.id !== character.id
            && (!item.source || item.source === 'episode')) ?? [];
        const reference = parents.length === 1 ? selectedImageEditReference(parents[0]) : null;
        if (!reference) return { kind: 'none' };
        const local = reference.url.startsWith('/') || /^[a-z]:[\\/]/i.test(reference.url)
            || /^(?:assets|storyboard|video|audio|export|uploads|playground|outputs?)\//.test(reference.url);
        return local ? { kind: 'local', ...reference } : { kind: 'none' };
    }
    for (const channel of ['three_view', 'headshot'] as const) {
        const unit = channel === 'three_view' ? character.three_view_asset : character.headshot_asset;
        if (!Array.isArray(unit?.variants)) continue;
        const variant = unit.variants.find(value => value.is_uploaded_source);
        if (!variant) continue;
        if (typeof variant.id !== 'string' || !variant.id || variant.id.trim() !== variant.id || /[\x00-\x1f\x7f]/.test(variant.id)
            || unit.variants.filter(value => value.id === variant.id).length !== 1) return { kind: 'invalid', channel };
        const refs = 'media_refs' in variant ? variant.media_refs : null;
        const raw = refs && typeof refs === 'object' && Object.hasOwn(refs, 'url') && 'url' in refs ? refs.url : variant.url;
        if (typeof raw !== 'string' || !raw || raw.trim() !== raw || /[\x00-\x1f\x7f]/.test(raw)
            || raw.startsWith('//')) return { kind: 'invalid', channel };
        if (/^https?:\/\//.test(raw)) return { kind: 'legacy', channel, id: variant.id, url: raw };
        const drive = /^[a-z]:[\\/]/i.test(raw);
        if (/^[a-z][a-z0-9+.-]*:/i.test(raw) && !drive || raw.split(/[\\/]/).includes('..')
            || raw.includes('\\') && !drive) return { kind: 'invalid', channel };
        if (raw.startsWith('/') || drive || /^(?:assets|storyboard|video|audio|export|uploads|playground|outputs?)\//.test(raw)) {
            // The server alone can prove that an absolute candidate is under its managed root.
            return { kind: 'local', channel, id: variant.id, url: raw };
        }
        // Custom OSS prefixes are server configuration. Keep their existing admission path;
        // the old bridge must reject unknown keys before any paid fallback.
        return raw.includes('/') ? { kind: 'legacy', channel, id: variant.id, url: raw } : { kind: 'invalid', channel };
    }
    return { kind: 'none' };
}

interface CharacterWorkbenchProps {
    asset: any;
    onClose: () => void;
    onUpdateDescription: (desc: string) => void;
    onGenerate: (type: string, prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => void;
    generatingTypes: { type: string; batchSize: number }[];
    stylePrompt?: string;
    styleNegativePrompt?: string;
    onGenerateVideo?: (prompt: string, duration: number, subType?: string, audioUrl?: string) => void;
    onDeleteVideo?: (videoId: string) => void;
    isGeneratingVideo?: boolean;
}

export default function CharacterWorkbench({ asset, onClose, onUpdateDescription, onGenerate, generatingTypes = [], stylePrompt = "", styleNegativePrompt = "", onGenerateVideo, onDeleteVideo, isGeneratingVideo }: CharacterWorkbenchProps) {
    const tc = useTranslations("character");
    const feedback = useTranslations("castWorkbench");
    const motionFeedback = useTranslations("motionRequest");
    const common = useTranslations("common");
    const [activePanel, setActivePanel] = useState<"full_body" | "three_view" | "headshot" | "video">("full_body");
    const currentProject = useProjectStore(state => state.currentProject);
    const currentSeries = useProjectStore(state => state.currentSeries);
    const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
    const authorScope = environment ? JSON.stringify([environment.key, currentProject?.id,
        currentProject?.series_id, asset.id, asset.source]) : null;
    const model = currentProject?.model_settings?.t2i_model || 'wan2.1-t2i';
    const derivedImageModel = currentProject?.model_settings?.i2i_model || '';
    const wanThreeViewModel = isStudioWanImageModel(derivedImageModel);
    const qwenThreeViewModel = isStudioQwenImageModel(derivedImageModel);
    const threeViewReference: DerivedImageReference | null = wanThreeViewModel || qwenThreeViewModel
        ? asset.base_character_id ? null : selectedImageEditReference(asset)
        : localDerivedImageReference(asset, 'three_view');
    const wanThreeView = wanThreeViewModel && !!threeViewReference;
    const qwenThreeView = qwenThreeViewModel && !!threeViewReference;
    const strictThreeView = wanThreeView || qwenThreeView || usesStudioGptThreeView('character', derivedImageModel, 'three_view');
    const headshotReference = localDerivedImageReference(asset, 'headshot');
    const headshotFallback = derivedImageFallback(asset, 'headshot');
    const coreThreeView = strictThreeView && !!threeViewReference;
    const strictHeadshot = usesStudioGptHeadshot('character', derivedImageModel, 'headshot');
    const headshotSelectionInvalid = !!asset.full_body_asset?.selected_id
        && asset.full_body_asset.variants?.filter((variant: { id: string }) => variant.id === asset.full_body_asset.selected_id).length !== 1;
    const coreHeadshot = strictHeadshot && !!headshotReference;
    const fullBodyEligible = isStudioFullBodyEligible(asset);
    const fullBodySource = fullBodyEditSource(asset, currentProject);
    const fullBodyEditCandidate = isStudioGptImageModel(derivedImageModel) && fullBodySource.kind !== 'none';
    const coreFullBodyEdit = fullBodyEditCandidate && fullBodySource.kind === 'local';
    const fullBodySourceInvalid = fullBodyEditCandidate && fullBodySource.kind === 'invalid';
    const fullBodyAspectRatio = currentProject?.model_settings?.character_aspect_ratio || '9:16';
    const strictFullBody = coreFullBodyEdit || fullBodyEligible && usesStudioImageCore('character', model, 'full_body');
    const qwenFullBody = !coreFullBodyEdit && strictFullBody && usesStudioQwenImageCore('character', model, 'full_body');
    const gptFullBodyT2I = !coreFullBodyEdit && strictFullBody && isStudioGptImageModel(model);
    const freshFullBody = qwenFullBody || gptFullBodyT2I || coreFullBodyEdit;
    const [closing, setClosing] = useState(false);
    const motionIdentity = useMemo(() => ({ projectId: currentProject?.id, seriesId: currentProject?.series_id,
        assetId: asset.id, source: asset.source, environment }), [currentProject?.id, currentProject?.series_id, asset.id, asset.source, environment]);
    const motionLatest = useRef(motionIdentity); motionLatest.current = motionIdentity;
    const motionLifecycle = useRef<{ identity: object } | null>(null);
    const motionDraftEpoch = useRef(0);
    const audioUpload = useRef<object | null>(null);
    const [motionPreflightFailed, setMotionPreflightFailed] = useState<object | null>(null);
    const motionCurrent = (lease = motionLifecycle.current) => !!lease && lease.identity === motionIdentity
        && motionLifecycle.current === lease && motionLatest.current === motionIdentity && !closing && !!environment
        && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    const motionCleanEpoch = useRef(0);
    const motionAuthorScope = useRef<string | null>(null);
    const motionCreatedEpochs = useRef(new Map<string, number>());
    const motionRequests = useStudioMotionRequests({ open: !closing, projectId: currentProject?.id,
        assetId: asset.id, source: asset.source, seriesId: currentProject?.series_id,
        onCreated: journal => {
            if (!motionCurrent()) throw new ProjectReadbackError('Motion author session changed');
            motionCreatedEpochs.current.set(journal.request.client_request_id, motionDraftEpoch.current);
        },
        readback: async (result, isCurrent) => {
            const lease = motionLifecycle.current;
            const epoch = motionCreatedEpochs.current.get(result.journal.request.client_request_id) ?? motionCleanEpoch.current;
            const authorCurrent = () => motionCurrent(lease) && motionDraftEpoch.current === epoch;
            if (!authorCurrent()) throw new ProjectReadbackError('Motion author draft changed');
            const current = (snapshot: Project) => authorCurrent() && isCurrent(snapshot);
            const snapshot = await readProjectOnce(result.journal.projectId, undefined, { isCurrent: current });
            if (!current(snapshot)) throw new ProjectReadbackError('Motion result is no longer current');
            return snapshot;
        } });
    const derivedImageOwner = useMemo(() => ({ projectId: currentProject?.id, seriesId: currentProject?.series_id,
        assetId: asset.id, source: asset.source, environment }), [currentProject?.id, currentProject?.series_id, asset.id, asset.source, environment]);
    const derivedImageIdentity = useMemo(() => ({ owner: derivedImageOwner, model: derivedImageModel,
        threeViewId: threeViewReference?.id, threeViewUrl: threeViewReference?.url, threeViewSource: threeViewReference?.source,
        headshotId: headshotReference?.id, headshotUrl: headshotReference?.url, headshotSource: headshotReference?.source }),
    [derivedImageOwner, derivedImageModel, threeViewReference?.id, threeViewReference?.url, threeViewReference?.source,
        headshotReference?.id, headshotReference?.url, headshotReference?.source]);
    const derivedImageLatest = useRef(derivedImageIdentity); derivedImageLatest.current = derivedImageIdentity;
    const derivedImageLifecycle = useRef<{ identity: object } | null>(null);
    const derivedImageObserved = useRef<{ owner: object; identity: object; authorScope: string | null } | null>(null);
    const derivedImageDraftEpoch = useRef(0), derivedImageCleanEpoch = useRef(0);
    const derivedImageCreatedEpochs = useRef(new Map<string, number>());
    const threeViewPending = useRef<object | null>(null);
    const headshotPending = useRef<object | null>(null);
    const [headshotPreparing, setHeadshotPreparing] = useState<{ identity: object; batchSize: number } | null>(null);
    const [headshotPreflightFailed, setHeadshotPreflightFailed] = useState<object | null>(null);
    const [threeViewPreparing, setThreeViewPreparing] = useState<{ identity: object; batchSize: number } | null>(null);
    const [threeViewPreflightFailed, setThreeViewPreflightFailed] = useState<object | null>(null);
    const derivedImageCurrent = (lease = derivedImageLifecycle.current) => !!lease && lease.identity === derivedImageIdentity
        && derivedImageLifecycle.current === lease && derivedImageLatest.current === derivedImageIdentity && !closing && !!environment
        && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    useEffect(() => {
        const lease = { identity: derivedImageIdentity }; derivedImageLifecycle.current = lease;
        // Repeated effect setup is not an edit; model/reference ABA still advances the draft epoch.
        if (derivedImageObserved.current?.identity !== derivedImageIdentity) {
            const wasClean = derivedImageDraftEpoch.current === derivedImageCleanEpoch.current;
            derivedImageDraftEpoch.current += 1;
            if (derivedImageObserved.current?.owner !== derivedImageOwner) {
                // Preserve drafts through reconnects, but load saved text for a different owner.
                if (wasClean || (authorScope !== null && authorScope !== derivedImageObserved.current?.authorScope)) {
                    derivedImageCleanEpoch.current = derivedImageDraftEpoch.current;
                }
                derivedImageCreatedEpochs.current.clear();
            }
            derivedImageObserved.current = { owner: derivedImageOwner, identity: derivedImageIdentity,
                authorScope: authorScope ?? derivedImageObserved.current?.authorScope ?? null };
        }
        setThreeViewPreflightFailed(null); setHeadshotPreflightFailed(null);
        return () => { if (derivedImageLifecycle.current === lease) derivedImageLifecycle.current = null; };
    }, [derivedImageIdentity, derivedImageOwner, authorScope]);
    const threeViewRequests = useStudioAssetImageRequests({ kind: 'character', channel: 'three_view', open: !closing,
        projectId: currentProject?.id, assetId: asset.id, source: asset.source, seriesId: currentProject?.series_id,
        isDerivedImageReadbackCurrent: result => derivedImageCurrent()
            && derivedImageDraftEpoch.current === (derivedImageCreatedEpochs.current.get(result.journal.request.client_request_id) ?? derivedImageCleanEpoch.current)
            && (result.journal.request.model_name == null || result.journal.request.model_name === derivedImageModel)
            && (result.journal.request.reference_variant_id ?? null) === (threeViewReference?.id ?? null)
            && result.journal.request.reference_image_url === threeViewReference?.url
            && result.journal.request.reference_source === threeViewReference?.source });
    const headshotRequests = useStudioAssetImageRequests({ kind: 'character', channel: 'headshot', open: !closing,
        projectId: currentProject?.id, assetId: asset.id, source: asset.source, seriesId: currentProject?.series_id,
        isDerivedImageReadbackCurrent: result => derivedImageCurrent()
            && derivedImageDraftEpoch.current === (derivedImageCreatedEpochs.current.get(result.journal.request.client_request_id) ?? derivedImageCleanEpoch.current)
            && (result.journal.request.model_name == null || result.journal.request.model_name === derivedImageModel)
            && (result.journal.request.reference_variant_id ?? null) === (headshotReference?.id ?? null)
            && result.journal.request.reference_image_url === headshotReference?.url
            && result.journal.request.reference_source === headshotReference?.source });
    const legacyThreeViewBlocked = threeViewRequests.reading || threeViewRequests.readFailed
        || threeViewRequests.busy || threeViewRequests.unknown;
    const pendingThreeViewBatchSize = threeViewRequests.results.reduce((total, result) => total + (result.status === 'confirmed'
        && result.receipt?.request.members.some(member => member.status === 'pending' || member.status === 'processing')
        ? result.journal.request.batch_size ?? 1 : 0), 0);
    const legacyHeadshotBlocked = headshotRequests.reading || headshotRequests.readFailed || headshotRequests.busy || headshotRequests.unknown;
    const pendingHeadshotBatchSize = headshotRequests.results.reduce((total, result) => total + (result.status === 'confirmed'
        && result.receipt?.request.members.some(member => member.status === 'pending' || member.status === 'processing')
        ? result.journal.request.batch_size ?? 1 : 0), 0);
    const identity = useMemo(() => ({ owner: derivedImageOwner, model, i2iModel: derivedImageModel, fullBodyEligible,
        sourceKind: fullBodySource.kind, sourceChannel: fullBodySource.channel, referenceId: fullBodySource.id,
        referenceUrl: fullBodySource.url, parentId: asset.base_character_id, aspectRatio: fullBodyAspectRatio }),
    [derivedImageOwner, model, derivedImageModel, fullBodyEligible, fullBodySource.kind, fullBodySource.channel,
        fullBodySource.id, fullBodySource.url, asset.base_character_id, fullBodyAspectRatio]);
    const latest = useRef(identity); latest.current = identity;
    const lifecycle = useRef<object | null>(null);
    const draftEpoch = useRef(0), fullBodyCleanEpoch = useRef(0);
    const fullBodyObserved = useRef<{ owner: object; identity: object; authorScope: string | null } | null>(null);
    const fullBodyCreatedEpochs = useRef(new Map<string, number>());
    const pending = useRef<object | null>(null);
    const [preparing, setPreparing] = useState<{ identity: object; batchSize: number } | null>(null);
    const [preflightFailed, setPreflightFailed] = useState<object | null>(null);
    const fullBodyCurrent = (lease = lifecycle.current) => !!lease && lifecycle.current === lease && latest.current === identity
        && !closing && !!environment && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    useEffect(() => {
        const lease = {}; lifecycle.current = lease;
        if (fullBodyObserved.current?.identity !== identity) {
            const wasClean = draftEpoch.current === fullBodyCleanEpoch.current;
            draftEpoch.current += 1;
            if (fullBodyObserved.current?.owner !== derivedImageOwner) {
                if (wasClean || (authorScope !== null && authorScope !== fullBodyObserved.current?.authorScope)) {
                    fullBodyCleanEpoch.current = draftEpoch.current;
                }
                fullBodyCreatedEpochs.current.clear();
            }
            fullBodyObserved.current = { owner: derivedImageOwner, identity,
                authorScope: authorScope ?? fullBodyObserved.current?.authorScope ?? null };
        }
        setPreflightFailed(null);
        return () => { if (lifecycle.current === lease) lifecycle.current = null; };
    }, [identity, derivedImageOwner, authorScope]);
    const requests = useStudioAssetImageRequests({ kind: 'character', channel: 'full_body', open: !closing,
        projectId: currentProject?.id, assetId: asset.id, source: asset.source, seriesId: currentProject?.series_id,
        isDerivedImageReadbackCurrent: (result, snapshot) => {
            if (!isStudioFullBodyEditRequest(result.journal.request) || !coreFullBodyEdit || !fullBodyCurrent()
                || draftEpoch.current !== (fullBodyCreatedEpochs.current.get(result.journal.request.client_request_id) ?? fullBodyCleanEpoch.current)) return false;
            const root = snapshot ?? useProjectStore.getState().currentProject;
            if (!root || root.id !== currentProject?.id || (root.series_id ?? null) !== (currentProject?.series_id ?? null)
                || root.model_settings?.i2i_model !== derivedImageModel
                || (root.model_settings?.character_aspect_ratio || '9:16') !== fullBodyAspectRatio) return false;
            const targets = root.characters.filter(item => item.id === asset.id && item.source === asset.source);
            const reference = targets.length === 1 && targets[0].base_character_id === asset.base_character_id
                ? fullBodyEditSource(targets[0], root) : null;
            return reference?.kind === 'local' && reference.id === fullBodySource.id && reference.url === fullBodySource.url
                && reference.channel === fullBodySource.channel
                && (result.journal.request.model_name == null || result.journal.request.model_name === derivedImageModel)
                && (result.journal.request.aspect_ratio == null || result.journal.request.aspect_ratio === fullBodyAspectRatio)
                && result.journal.request.reference_variant_id === reference.id
                && result.journal.request.reference_image_url === reference.url;
        } });
    const fullBodyBlocked = requests.reading || requests.readFailed || requests.busy || requests.unknown || requests.readbackFailed;
    const fullBodyHasJournal = (exceptKey?: string) => {
        if (!environment || !currentProject) return true;
        try {
            return listStudioAssetImageRequests({ environment, rootKind: 'project', rootId: currentProject.id })
                .some(journal => journal.request.asset_id === asset.id && journal.request.asset_type === 'character'
                    && journal.request.generation_type === 'full_body' && journal.request.client_request_id !== exceptKey);
        } catch { return true; }
    };
    const pendingFullBodyBatchSize = requests.results.reduce((total, result) => total + (result.status === 'confirmed'
        && result.receipt?.request.members.some(member => member.status === 'pending' || member.status === 'processing')
        ? result.journal.request.batch_size ?? 1 : 0), 0);
    const close = () => {
        // AnimatePresence may retain the component during its exit transition.
        lifecycle.current = null; motionLifecycle.current = null; derivedImageLifecycle.current = null; recoveryEpoch.current += 1; setClosing(true); onClose();
    };

    const recoveryEpoch = useRef(0);
    const [readingAsset, setReadingAsset] = useState(false);
    const [assetReadFailed, setAssetReadFailed] = useState(false);
    useEffect(() => { recoveryEpoch.current += 1; setReadingAsset(false); setAssetReadFailed(false); return () => { recoveryEpoch.current += 1; }; }, [currentProject?.id, asset.id, asset.source, currentProject?.series_id]);
    useEffect(() => subscribeRuntimeEnvironment(() => {
        recoveryEpoch.current += 1; setReadingAsset(false); setAssetReadFailed(false);
    }), []);
    const readAssetAgain = async () => {
        if (!currentProject) return;
        const epoch = recoveryEpoch.current;
        const environment = getRuntimeEnvironment();
        if (!environment) { setAssetReadFailed(true); return; }
        const live = () => epoch === recoveryEpoch.current && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        setReadingAsset(true); setAssetReadFailed(false);
        try { await readProjectOnce(currentProject.id, undefined, { isCurrent: snapshot => live() && (asset.source !== 'series' || snapshot.series_id === currentProject.series_id) && snapshot.characters.some(item => item.id === asset.id && item.source === asset.source) }); }
        catch { if (live()) setAssetReadFailed(true); }
        finally { if (live()) setReadingAsset(false); }
    };

    // Mode state for Asset Activation v2 (Static/Motion)
    const [fullBodyMode, setFullBodyMode] = useState<'static' | 'motion'>('static');
    const [headshotMode, setHeadshotMode] = useState<'static' | 'motion'>('static');

    // Motion Ref prompts (initialized with PRD templates)
    const [fullBodyMotionPrompt, setFullBodyMotionPrompt] = useState('');
    const [headshotMotionPrompt, setHeadshotMotionPrompt] = useState('');

    // Motion Ref audio URLs
    const [fullBodyAudioUrl, setFullBodyAudioUrl] = useState('');
    const [headshotAudioUrl, setHeadshotAudioUrl] = useState('');
    const [isUploadingAudio, setIsUploadingAudio] = useState(false);

    // Motion Ref generation state
    const [isVideoLoading, setIsVideoLoading] = useState(false);

    useEffect(() => {
        const lease = { identity: motionIdentity }; motionLifecycle.current = lease;
        const wasClean = motionDraftEpoch.current === motionCleanEpoch.current;
        motionDraftEpoch.current += 1;
        if (wasClean || (authorScope !== null && authorScope !== motionAuthorScope.current)) {
            motionCleanEpoch.current = motionDraftEpoch.current;
        }
        motionAuthorScope.current = authorScope ?? motionAuthorScope.current;
        motionCreatedEpochs.current.clear(); audioUpload.current = null;
        setFullBodyAudioUrl(''); setHeadshotAudioUrl(''); setIsUploadingAudio(false); setMotionPreflightFailed(null);
        return () => { if (motionLifecycle.current === lease) motionLifecycle.current = null; };
    }, [motionIdentity, authorScope]);


    // === Reverse Generation: Detect uploaded images ===
    const hasUploadedThreeViews = asset.three_view_asset?.variants?.some((v: any) => v.is_uploaded_source) || false;
    const hasUploadedHeadshot = asset.headshot_asset?.variants?.some((v: any) => v.is_uploaded_source) || false;
    const hasUploadedFullBody = asset.full_body_asset?.variants?.some((v: any) => v.is_uploaded_source) || false;
    const hasAnyUpload = hasUploadedThreeViews || hasUploadedHeadshot || hasUploadedFullBody;
    const hasNonFullBodyUpload = hasUploadedThreeViews || hasUploadedHeadshot;
    const headshotReferenceAvailable = !headshotSelectionInvalid
        && (strictHeadshot && !asset.base_character_id && !asset.full_body_asset?.selected_id
            ? headshotFallback.kind === 'local' || headshotFallback.kind === 'legacy'
            : !!asset.full_body_asset?.selected_id || !!asset.full_body_image_url || hasUploadedThreeViews || hasUploadedHeadshot);
    const hasFullBodyImage = !!(asset.full_body_image_url || (asset.full_body_asset?.variants?.length > 0));

    // Local state for prompts
    const getInitialPrompt = (type: string, existingPrompt: string) => {
        if (existingPrompt) return existingPrompt;

        const baseDesc = asset.description || "";
        const name = asset.name || "Character";

        if (type === "full_body") {
            const prefix = hasNonFullBodyUpload ? "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, and clothing as the reference image. " : "";
            return `${prefix}Full body character design of ${name}, concept art. ${baseDesc}. Standing pose, neutral expression, no emotion, looking at viewer. Clean white background, isolated, no other objects, no scenery, simple background, high quality, masterpiece.`;
        }
        if (type === "three_view") {
            const prefix = (hasFullBodyImage || hasAnyUpload) ? "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, and clothing as the reference image. " : "";
            return `${prefix}Character Reference Sheet for ${name}. ${baseDesc}. Three-view character design: Front view, Side view, and Back view. Full body, standing pose, neutral expression. Consistent clothing and details across all views. Simple white background, clean lines, studio lighting, high quality.`;
        }
        if (type === "headshot") {
            const prefix = (hasFullBodyImage || hasAnyUpload) ? "STRICTLY MAINTAIN the SAME face, hairstyle, skin tone, and facial features as the reference image. " : "";
            return `${prefix}Close-up portrait of the SAME character ${name}. ${baseDesc}. Zoom in on face and shoulders, detailed facial features, neutral expression, looking at viewer, high quality, masterpiece.`;
        }
        return "";
    };

    const [fullBodyPrompt, setFullBodyPrompt] = useState(getInitialPrompt("full_body", asset.full_body_prompt));
    const [threeViewPrompt, setThreeViewPrompt] = useState(getInitialPrompt("three_view", asset.three_view_prompt));
    const [headshotPrompt, setHeadshotPrompt] = useState(getInitialPrompt("headshot", asset.headshot_prompt));
    const [videoPrompt, setVideoPrompt] = useState(asset.video_prompt || "");

    // New State for Style Control
    const [applyStyle, setApplyStyle] = useState(true);
    // User's own negative prompt (initially empty or with sensible defaults)
    const [negativePrompt, setNegativePrompt] = useState("low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, jpeg artifacts, signature, watermark, blurry");
    // Art Direction Style expanded state (collapsed by default to save space)
    const [showStyleExpanded, setShowStyleExpanded] = useState(false);
    const inheritedStyleCurrent = currentSeries?.id === currentProject?.series_id && (!freshFullBody
        || currentSeries?.revision === currentProject?.read_versions?.series?.revision && !currentProject?.sharedAssetsNeedRefresh);
    const effectiveStyle = (currentProject?.art_direction ?? (inheritedStyleCurrent ? currentSeries?.art_direction : null))?.style_config;
    const negativeBlocked = (styleNegative: string) => qwenFullBody
        ? Array.from([negativePrompt, applyStyle ? styleNegative : ''].filter(Boolean).join(', ')).length > 500
        : strictFullBody && (!!negativePrompt || !gptFullBodyT2I && applyStyle && !!styleNegative);
    const fullBodyNegativeBlocked = negativeBlocked(effectiveStyle?.negative_prompt || '');
    const threeViewStyleNegativeBlocked = wanThreeView && applyStyle && !!effectiveStyle?.negative_prompt;

    const generateFullBody = async (prompt: string, batchSize: number) => {
        const lease = lifecycle.current;
        const capturedDraftEpoch = draftEpoch.current;
        const reference = coreFullBodyEdit && fullBodySource.kind === 'local' ? fullBodySource : null;
        if (!currentProject || !environment || !lease || closing || pending.current === lease || fullBodyBlocked
            || fullBodySourceInvalid || fullBodyNegativeBlocked || getGeneratingInfo('full_body').isGenerating) return;
        const sessionCurrent = () => fullBodyCurrent(lease);
        const live = () => sessionCurrent() && (!freshFullBody || capturedDraftEpoch === draftEpoch.current);
        const ownerMatches = (snapshot: Project) => {
            if (snapshot.id !== currentProject.id || snapshot.series_id !== currentProject.series_id) return false;
            if (!coreFullBodyEdit) return (snapshot.model_settings?.t2i_model || 'wan2.1-t2i') === model
                && (!gptFullBodyT2I || (snapshot.model_settings?.character_aspect_ratio || '9:16') === fullBodyAspectRatio)
                && snapshot.characters.some(item => item.id === asset.id && item.source === asset.source && isStudioFullBodyEligible(item));
            if (!reference || snapshot.model_settings?.i2i_model !== derivedImageModel
                || (snapshot.model_settings?.character_aspect_ratio || '9:16') !== fullBodyAspectRatio) return false;
            const targets = snapshot.characters.filter(item => item.id === asset.id && item.source === asset.source);
            const chosen = targets.length === 1 && targets[0].base_character_id === asset.base_character_id
                ? fullBodyEditSource(targets[0], snapshot) : null;
            return chosen?.kind === 'local' && chosen.channel === reference.channel && chosen.id === reference.id && chosen.url === reference.url;
        };
        let createdKey: string | undefined;
        pending.current = lease; setPreparing({ identity, batchSize }); setPreflightFailed(null);
        try {
            const fresh = await readProjectOnce(currentProject.id, undefined, { isCurrent: snapshot => live() && ownerMatches(snapshot) });
            if (!live()) return;
            let freshStyle = fresh.art_direction?.style_config;
            if (applyStyle && !fresh.art_direction && fresh.series_id) {
                const series = await api.getSeries(fresh.series_id);
                if (!live()) return;
                // Reuse the existing captured-series revision check; never infer a missing baseline from stale cache.
                if (!useProjectStore.getState().applySeriesReadSnapshot(fresh.id, fresh.series_id, series)) throw new ProjectReadbackError('Series style is not current');
                freshStyle = useProjectStore.getState().currentSeries?.art_direction?.style_config;
            }
            if (!live()) return;
            const current = useProjectStore.getState().currentProject;
            if (!current || current.id !== fresh.id || !ownerMatches(current)) throw new ProjectReadbackError('Asset is no longer current');
            const styleAtSubmission = () => {
                const { currentProject: root, currentSeries: series } = useProjectStore.getState();
                return root?.art_direction ?? (series?.id === root?.series_id ? series?.art_direction : null);
            };
            const capturedStyle = JSON.stringify(applyStyle ? styleAtSubmission() : null);
            const submissionCurrent = () => {
                const state = useProjectStore.getState(), root = state.currentProject, series = state.currentSeries;
                if (!live() || !root || root.id !== fresh.id || !ownerMatches(root) || root.sharedAssetsNeedRefresh
                    || fullBodyHasJournal(createdKey)) return false;
                if (freshFullBody && (root.readEnvironment !== environment.key
                    || (coreFullBodyEdit || gptFullBodyT2I ? !sameReadVersion(root, fresh) : root.revision !== fresh.revision)
                    || capturedStyle !== JSON.stringify(applyStyle ? styleAtSubmission() : null))) return false;
                if (applyStyle && !root.art_direction && root.series_id
                    && (!series || series.id !== root.series_id || series.revision !== root.read_versions?.series?.revision)) return false;
                const style = (root.art_direction ?? (series?.id === root.series_id ? series?.art_direction : null))?.style_config;
                return !negativeBlocked(style?.negative_prompt || '');
            };
            if (!submissionCurrent()) return;
            if (useProjectStore.getState().generatingTasks.some(task => task.assetId === asset.id
                && (task.generationType === 'full_body' || task.generationType === 'all') && isAssetGenerationActive(task))) return;
            await requests.submit({ asset_id: asset.id, asset_type: 'character', generation_type: 'full_body',
                style_preset: 'ArtDirection', style_prompt: freshFullBody && !applyStyle ? '' : freshStyle?.positive_prompt || '', prompt,
                apply_style: applyStyle, negative_prompt: negativePrompt, batch_size: batchSize,
                model_name: coreFullBodyEdit ? derivedImageModel : model,
                ...(reference ? { reference_variant_id: reference.id, reference_image_url: reference.url, aspect_ratio: fullBodyAspectRatio } : {}) },
            submissionCurrent, journal => {
                if (!coreFullBodyEdit) return;
                if (!live()) throw new ProjectReadbackError('Full-body author session changed');
                createdKey = journal.request.client_request_id;
                fullBodyCreatedEpochs.current.set(createdKey, capturedDraftEpoch);
            });
        } catch { if (live()) setPreflightFailed(identity); }
        finally {
            if (pending.current === lease) pending.current = null;
            if (sessionCurrent()) setPreparing(null);
        }
    };

    const generateDerivedImage = async (channel: 'three_view' | 'headshot', prompt: string, batchSize: number) => {
        const headshot = channel === 'headshot';
        const channelPending = headshot ? headshotPending : threeViewPending;
        const channelRequests = headshot ? headshotRequests : threeViewRequests;
        const setChannelPreparing = headshot ? setHeadshotPreparing : setThreeViewPreparing;
        const setChannelPreflightFailed = headshot ? setHeadshotPreflightFailed : setThreeViewPreflightFailed;
        const lease = derivedImageLifecycle.current, epoch = derivedImageDraftEpoch.current;
        const reference = headshot ? headshotReference : threeViewReference;
        if (!(headshot ? strictHeadshot : strictThreeView) || !currentProject || !environment || !reference || !derivedImageCurrent(lease)
            || channelPending.current === lease || channelRequests.busy || !!negativePrompt && (headshot || !qwenThreeView)
            || !headshot && threeViewStyleNegativeBlocked || getGeneratingInfo(channel).isGenerating) return;
        const live = () => derivedImageCurrent(lease) && derivedImageDraftEpoch.current === epoch;
        const ownerMatches = (snapshot: Project) => {
            if (snapshot.id !== currentProject.id || (snapshot.series_id ?? null) !== (currentProject.series_id ?? null)
                || snapshot.model_settings?.i2i_model !== derivedImageModel) return false;
            const targets = snapshot.characters.filter(item => item.id === asset.id && item.source === asset.source);
            const selected: DerivedImageReference | null = targets.length === 1 ? !headshot && (wanThreeViewModel || qwenThreeViewModel)
                ? targets[0].base_character_id ? null : selectedImageEditReference(targets[0])
                : localDerivedImageReference(targets[0], channel) : null;
            return !!selected && selected.id === reference.id && selected.url === reference.url && selected.source === reference.source;
        };
        channelPending.current = lease; setChannelPreparing({ identity: derivedImageIdentity, batchSize }); setChannelPreflightFailed(null);
        try {
            const fresh = await readProjectOnce(currentProject.id, undefined, { isCurrent: snapshot => live() && ownerMatches(snapshot) });
            if (!live() || !ownerMatches(fresh)) return;
            let freshStyle = fresh.art_direction?.style_config;
            if (applyStyle && !fresh.art_direction && fresh.series_id) {
                const series = await api.getSeries(fresh.series_id);
                if (!live()) return;
                if (!useProjectStore.getState().applySeriesReadSnapshot(fresh.id, fresh.series_id, series)) throw new ProjectReadbackError('Series style is not current');
                freshStyle = useProjectStore.getState().currentSeries?.art_direction?.style_config;
            }
            if (!headshot && wanThreeView && applyStyle && freshStyle?.negative_prompt) return;
            const styleAtSubmission = () => {
                const { currentProject: root, currentSeries: series } = useProjectStore.getState();
                return root?.art_direction ?? (series?.id === root?.series_id ? series?.art_direction : null);
            };
            const capturedStyle = JSON.stringify(applyStyle ? styleAtSubmission() : null);
            const submissionCurrent = () => {
                const { currentProject: root, currentSeries: series, generatingTasks } = useProjectStore.getState();
                if (!live() || !root || !ownerMatches(root) || root.sharedAssetsNeedRefresh
                    || root.readEnvironment !== environment.key || !sameReadVersion(root, fresh)
                    || capturedStyle !== JSON.stringify(applyStyle ? styleAtSubmission() : null)) return false;
                if (applyStyle && !root.art_direction && root.series_id
                    && (!series || series.id !== root.series_id || series.revision !== root.read_versions?.series?.revision)) return false;
                return !generatingTasks.some(task => task.assetId === asset.id
                    && (task.generationType === channel || task.generationType === 'all') && isAssetGenerationActive(task));
            };
            if (!submissionCurrent()) return;
            await channelRequests.submit({ asset_id: asset.id, asset_type: 'character', generation_type: channel,
                model_name: derivedImageModel, reference_image_url: reference.url,
                ...(reference.id === undefined ? {} : { reference_variant_id: reference.id }),
                ...(reference.source === undefined ? {} : { reference_source: reference.source }),
                style_preset: 'ArtDirection', style_prompt: applyStyle ? freshStyle?.positive_prompt || '' : '', prompt,
                apply_style: applyStyle, negative_prompt: negativePrompt, batch_size: batchSize }, submissionCurrent, journal => {
                if (!live()) throw new ProjectReadbackError('Derived-image author session changed');
                derivedImageCreatedEpochs.current.set(journal.request.client_request_id, epoch);
            });
        } catch { if (live()) setChannelPreflightFailed(derivedImageIdentity); }
        finally {
            if (channelPending.current === lease) channelPending.current = null;
            if (derivedImageCurrent(lease)) setChannelPreparing(null);
        }
    };

    // Get the uploaded image URL for reverse generation reference
    const getUploadedReferenceUrl = () => {
        if (hasUploadedThreeViews) {
            const uploadedVariant = asset.three_view_asset?.variants?.find((v: any) => v.is_uploaded_source);
            return uploadedVariant?.url || asset.three_view_image_url;
        }
        if (hasUploadedHeadshot) {
            const uploadedVariant = asset.headshot_asset?.variants?.find((v: any) => v.is_uploaded_source);
            return uploadedVariant?.url || asset.headshot_image_url;
        }
        return null;
    };

    // Motion Ref generation handler with validation
    const handleGenerateMotionRef = async (assetType: 'full_body' | 'head_shot', prompt: string, audioUrl?: string) => {
        const lease = motionLifecycle.current;
        if (!onGenerateVideo || !motionCurrent(lease) || isUploadingAudio) return;

        // Check if source image exists
        const hasSourceImage = assetType === 'full_body'
            ? (asset.full_body?.image_variants?.some((variant: { id: string; url?: string }) => variant.id === asset.full_body.selected_image_id && variant.url)
                || asset.full_body_image_url || asset.full_body_asset?.variants?.length > 0)
            : (asset.head_shot?.image_variants?.some((variant: { id: string; url?: string }) => variant.id === asset.head_shot.selected_image_id && variant.url)
                || asset.headshot_image_url || asset.headshot_asset?.variants?.length > 0);

        if (!hasSourceImage) {
            alert(tc('generateFirstStatic', { type: assetType === 'full_body' ? tc('fullBodyType') : tc('avatarType') }));
            return;
        }

        if (audioUrl) {
            setIsVideoLoading(true); onGenerateVideo(prompt, 5, assetType, audioUrl); return;
        }
        if (!currentProject || !motionRequests.owner || motionRequests.busy || motionRequests.blocked
            || generatingTypes.some(task => task.type === `video_${assetType}`)) return;
        const baseline = currentProject, epoch = motionDraftEpoch.current;
        const authorCurrent = () => {
            const root = useProjectStore.getState().currentProject;
            return motionCurrent(lease) && motionDraftEpoch.current === epoch && !!root && sameReadVersion(root, baseline)
                && !root.sharedAssetsNeedRefresh && root.series_id === baseline.series_id
                && root.characters.some(item => item.id === asset.id && item.source === asset.source);
        };
        setMotionPreflightFailed(null);
        await motionRequests.submit(async () => {
            try {
                // A same-owner read may refresh visible source state. The check
                // below still ends this click if its captured version changed.
                const fresh = await readProjectOnce(baseline.id, undefined, { isCurrent: snapshot => authorCurrent()
                    && snapshot.series_id === baseline.series_id
                    && snapshot.characters.some(item => item.id === asset.id && item.source === asset.source) });
                if (!authorCurrent() || !sameReadVersion(fresh, baseline)) throw new ProjectReadbackError('Motion source changed');
                const qualification = await readStudioMotionEligibility({ ...motionRequests.owner!, assetType, isCurrent: authorCurrent });
                if (!authorCurrent()) throw new ProjectReadbackError('Motion draft changed');
                if (!qualification.eligible) {
                    const legacy = ['model_unsupported', 'configured_audio_present', 'selected_image_required'].includes(qualification.reason ?? '')
                        || qualification.reason === 'source_kind_unsupported' && ['remote_url', 'object_key'].includes(qualification.source_kind);
                    if (!legacy) throw new ProjectReadbackError('Motion source cannot be confirmed');
                    return 'legacy';
                }
                const selected = fresh.characters.find(item => item.id === asset.id)?.[assetType]?.selected_image_id;
                if (selected !== qualification.selected_image_id) throw new ProjectReadbackError('Motion image changed');
                return { input: { asset_id: asset.id, asset_type: assetType, prompt, duration: 5, batch_size: 1,
                    expected_target: qualification.expected_target }, selectedImageId: qualification.selected_image_id! };
            } catch (error) { if (motionCurrent(lease)) setMotionPreflightFailed(motionIdentity); throw error; }
        }, authorCurrent, () => {
            setIsVideoLoading(true); onGenerateVideo(prompt, 5, assetType, audioUrl);
        });
    };


    // Audio upload handler for Motion Ref
    const handleAudioUpload = async (file: File, assetType: 'full_body' | 'head_shot') => {
        const lease = motionLifecycle.current;
        if (!file || !motionCurrent(lease)) return;

        // Validate file type
        if (!file.type.startsWith('audio/')) {
            alert(tc('invalidAudioFile'));
            return;
        }

        // Validate file size (max 10MB)
        if (file.size > 10 * 1024 * 1024) {
            alert(tc('audioTooLarge'));
            return;
        }

        const operation = {}; audioUpload.current = operation;
        const epoch = ++motionDraftEpoch.current;
        setIsUploadingAudio(true);

        try {
            const result = await api.uploadFile(file);
            if (!motionCurrent(lease) || audioUpload.current !== operation) return;
            const url = result.url;

            if (assetType === 'full_body') {
                setFullBodyAudioUrl(url);
                // Automatically update prompt if it's the default "counting" one
                const currentDefault = `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right. The character is speaking naturally, counting numbers from one to five in English.\nHead to toe shot, stable camera, flat lighting.`;
                const oldDefault = `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right to show costume details. No walking away.\nHead to toe shot, stable camera, flat lighting.`;

                if (motionDraftEpoch.current === epoch && (fullBodyMotionPrompt === currentDefault || fullBodyMotionPrompt === oldDefault || !fullBodyMotionPrompt)) {
                    setFullBodyMotionPrompt(`Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures, turning body 30 degrees left and right. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions.\nHead to toe shot, stable camera, flat lighting.`);
                }
            } else {
                setHeadshotAudioUrl(url);
                // Automatically update prompt if it's the default "counting" one
                const currentDefault = `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally, counting numbers from one to five in English, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;
                const oldDefault = `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally matching the audio, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;

                if (motionDraftEpoch.current === epoch && (headshotMotionPrompt === currentDefault || headshotMotionPrompt === oldDefault || !headshotMotionPrompt)) {
                    setHeadshotMotionPrompt(`High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally matching the audio, with accurate lip-sync and facial expressions, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`);
                }
            }
        } catch (error: any) {
            if (!motionCurrent(lease) || audioUpload.current !== operation) return;
            console.error('Failed to upload audio:', error);
            alert(tc('audioUploadFailed', { error: error.message }));
        } finally {
            if (audioUpload.current === operation) { audioUpload.current = null; if (motionCurrent(lease)) setIsUploadingAudio(false); }
        }
    };

    // PRD Motion Prompt Templates
    const getMotionDefault = (type: 'full_body' | 'headshot', hasAudio: boolean) => {
        if (type === 'full_body') {
            return hasAudio
                ? `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures, turning body 30 degrees left and right. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions.\nHead to toe shot, stable camera, flat lighting.`
                : `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right. The character is speaking naturally, counting numbers from one to five in English.\nHead to toe shot, stable camera, flat lighting.`;
        } else {
            return hasAudio
                ? `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally matching the audio, with accurate lip-sync and facial expressions, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`
                : `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally, counting numbers from one to five in English, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;
        }
    };

    // Initialize prompts if empty (first time load)
    useEffect(() => {
        // An explicitly empty prompt is also an authored draft.
        if (motionDraftEpoch.current !== motionCleanEpoch.current) return;
        if (!fullBodyPrompt) {
            setFullBodyPrompt(`Full body character design of ${asset.name}, concept art. ${asset.description}. Standing pose, neutral expression, no emotion, looking at viewer. Clean white background, isolated, no other objects, no scenery, simple background, high quality, masterpiece.`);
        }
        if (!threeViewPrompt && (!strictThreeView || derivedImageDraftEpoch.current === derivedImageCleanEpoch.current)) {
            setThreeViewPrompt(`Character Reference Sheet for ${asset.name}. ${asset.description}. Three-view character design: Front view, Side view, and Back view. Full body, standing pose, neutral expression. Consistent clothing and details across all views. Simple white background.`);
        }
        if (!headshotPrompt) {
            setHeadshotPrompt(`Close-up portrait of the SAME character ${asset.name}. ${asset.description}. Zoom in on face and shoulders, detailed facial features, neutral expression, looking at viewer, high quality, masterpiece.`);
        }
        if (!videoPrompt) {
            setVideoPrompt(`Cinematic shot of ${asset.name}, ${asset.description}, looking around, breathing, slight movement, high quality, 4k`);
        }

        if (!fullBodyMotionPrompt) {
            setFullBodyMotionPrompt(getMotionDefault('full_body', !!fullBodyAudioUrl));
        }
        if (!headshotMotionPrompt) {
            setHeadshotMotionPrompt(getMotionDefault('headshot', !!headshotAudioUrl));
        }
    }, [asset.name, asset.description, strictThreeView]);

    const handleResetMotionPrompt = (type: 'full_body' | 'headshot') => {
        if (!motionCurrent()) return;
        motionDraftEpoch.current += 1;
        const hasAudio = type === 'full_body' ? !!fullBodyAudioUrl : !!headshotAudioUrl;
        const defaultPrompt = getMotionDefault(type, hasAudio);
        if (type === 'full_body') {
            setFullBodyMotionPrompt(defaultPrompt);
        } else {
            setHeadshotMotionPrompt(defaultPrompt);
        }
    };


    // Update local state when asset updates (e.g. after generation)
    useEffect(() => {
        // Source refreshes may update media without replacing local author intent.
        if (motionDraftEpoch.current !== motionCleanEpoch.current) return;
        if (asset.full_body_prompt) setFullBodyPrompt(asset.full_body_prompt);
        else if (hasNonFullBodyUpload && !fullBodyPrompt.includes("STRICTLY MAINTAIN")) {
            setFullBodyPrompt(getInitialPrompt("full_body", ""));
        }

        if (!strictThreeView || derivedImageDraftEpoch.current === derivedImageCleanEpoch.current) {
            if (asset.three_view_prompt) setThreeViewPrompt(asset.three_view_prompt);
            else if (hasAnyUpload && !threeViewPrompt.includes("STRICTLY MAINTAIN")) {
                setThreeViewPrompt(getInitialPrompt("three_view", ""));
            }
        }

        if (asset.headshot_prompt) setHeadshotPrompt(asset.headshot_prompt);
        else if (hasAnyUpload && !headshotPrompt.includes("STRICTLY MAINTAIN")) {
            setHeadshotPrompt(getInitialPrompt("headshot", ""));
        }

        if (asset.video_prompt) setVideoPrompt(asset.video_prompt);
        if (asset.full_body?.video_prompt) setFullBodyMotionPrompt(asset.full_body.video_prompt);
        if (asset.head_shot?.video_prompt) setHeadshotMotionPrompt(asset.head_shot.video_prompt);
    }, [asset, hasAnyUpload, hasNonFullBodyUpload, strictThreeView, authorScope]);

    const handleGenerateClick = (type: "full_body" | "three_view" | "headshot", batchSize: number) => {
        if (closing) return;
        let prompt = "";
        if (type === "full_body") prompt = fullBodyPrompt;
        else if (type === "three_view") prompt = threeViewPrompt;
        else if (type === "headshot") prompt = headshotPrompt;

        if (type === 'full_body') {
            if (!fullBodyCurrent() || fullBodySourceInvalid || fullBodyBlocked) return;
            // A model/source change must not mint a second paid request while the original key is unresolved.
            if (fullBodyHasJournal()) { void requests.refresh(); return; }
            if (strictFullBody) { void generateFullBody(prompt, batchSize); return; }
        }
        if (type === 'three_view' || type === 'headshot') {
            const headshot = type === 'headshot';
            if (headshot ? coreHeadshot : coreThreeView) { void generateDerivedImage(type, prompt, batchSize); return; }
            if (!derivedImageCurrent() || (headshot ? !headshotReferenceAvailable || legacyHeadshotBlocked
                : strictThreeView || legacyThreeViewBlocked)) return;
            const channelRequests = headshot ? headshotRequests : threeViewRequests;
            // This old endpoint cannot carry a Core request key. Re-read the
            // original journal at dispatch, including records unseen by recovery.
            try {
                const unresolved = listStudioAssetImageRequests({ environment: environment!, rootKind: 'project', rootId: currentProject!.id })
                    .some(journal => journal.request.asset_id === asset.id && journal.request.asset_type === 'character'
                        && journal.request.generation_type === type
                        && !channelRequests.results.some(result => result.status === 'confirmed'
                            && result.journal.request.client_request_id === journal.request.client_request_id
                            && result.journal.receiptIdentity === journal.receiptIdentity
                            && serializeJournalData(result.journal.request) === serializeJournalData(journal.request)
                            && result.receipt?.request.members.every(member => member.status === 'completed' || member.status === 'cancelled')));
                if (unresolved) { void channelRequests.refresh(); return; }
            } catch { void channelRequests.refresh(); return; }
        }
        onGenerate(type, prompt, applyStyle, negativePrompt, batchSize);
    };

    // Helper to check if a specific type is generating
    const getGeneratingInfo = (type: string) => {
        if (!Array.isArray(generatingTypes) || generatingTypes.length === 0) {
            return { isGenerating: false, batchSize: 1 };
        }
        const task = generatingTypes.find(t => t?.type === type || t?.type === "all");
        return task ? { isGenerating: true, batchSize: task.batchSize || 1 } : { isGenerating: false, batchSize: 1 };
    };

    const handleSelectVariant = async (type: "full_body" | "three_view" | "headshot", variantId: string) => {
        if (!currentProject) return;
        if (type === 'full_body') { if (!motionCurrent()) return; motionDraftEpoch.current += 1; }
        derivedImageDraftEpoch.current += 1;

        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, asset.id, "character", variantId, type);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
        }
    };

    const handleDeleteVariant = async (type: "full_body" | "three_view" | "headshot", variantId: string) => {
        if (!currentProject) return;
        if (type === 'full_body') { if (!motionCurrent()) return; motionDraftEpoch.current += 1; }
        derivedImageDraftEpoch.current += 1;

        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, asset.id, "character", variantId);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
        }
    };

    const handleFavoriteVariant = async (type: "full_body" | "three_view" | "headshot", variantId: string, isFavorited: boolean) => {
        if (!currentProject) return;

        try {
            const updatedProject = await api.favoriteAssetVariant(currentProject.id, asset.id, "character", variantId, isFavorited, type);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to favorite variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
        }
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-7xl h-[90vh] flex flex-col overflow-hidden shadow-lg"
            >
                <div className="h-16 border-b border-glass-border flex justify-between items-center px-6 bg-surface">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-foreground">{asset.name} <span className="text-text-muted font-normal text-sm ml-2">{tc("workbench")}</span></h2>
                        <div className="flex items-center gap-2 px-3 py-1 bg-blue-500/10 border border-blue-500/20 rounded-full">
                            <span className="text-xs text-blue-400 font-medium">{tc("tipConsistency")}</span>
                        </div>
                    </div>
                    <button onClick={close} aria-label={common('close')} className="p-2 hover:bg-hover-bg rounded-full text-text-secondary hover:text-foreground transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {asset.status === 'needs_confirmation' && <AssetRecoveryNotice onRead={readAssetAgain} reading={readingAsset} readFailed={assetReadFailed} />}
                {/* Main Content - 3 Columns */}
                <div className="flex-1 flex overflow-hidden">

                    {/* Panel 1: Full Body (Master) */}
                    <WorkbenchPanel
                        title={tc("masterAsset")}
                        isActive={activePanel === "full_body"}
                        onClick={() => setActivePanel("full_body")}

                        asset={asset.full_body_asset}
                        currentImageUrl={asset.full_body_image_url}
                        onSelect={(id: string) => handleSelectVariant("full_body", id)}
                        onDelete={(id: string) => handleDeleteVariant("full_body", id)}
                        onFavorite={(id: string, isFav: boolean) => handleFavoriteVariant("full_body", id, isFav)}

                        prompt={fullBodyPrompt}
                        setPrompt={(value: string) => {
                            draftEpoch.current += 1;
                            if (value !== fullBodyPrompt) { motionDraftEpoch.current += 1; derivedImageDraftEpoch.current += 1; }
                            setFullBodyPrompt(value);
                        }}
                        onGenerate={(batchSize: number) => handleGenerateClick("full_body", batchSize)}
                        onBatchSizeChange={freshFullBody ? () => {
                            if (!environment || closing || !lifecycle.current || latest.current !== identity
                                || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return false;
                            draftEpoch.current += 1; return true;
                        } : undefined}
                        isGenerating={getGeneratingInfo("full_body").isGenerating || requests.busy || preparing?.identity === identity}
                        generateDisabled={fullBodyNegativeBlocked || fullBodySourceInvalid || fullBodyBlocked || closing}
                        requestResults={<div className="space-y-2 mt-2 text-xs">
                            {fullBodySourceInvalid && <p role="status" className="text-amber-300">{feedback('fullBodyReferenceInvalid')}</p>}
                            {qwenFullBody && fullBodyNegativeBlocked && <p role="status" className="text-amber-300">{feedback('fullBodyNegativeTooLong')}</p>}
                            {strictFullBody && !qwenFullBody && !!negativePrompt && <div className="text-amber-300"><p>{feedback('negativeDraftUnsupported')}</p>
                                <button onClick={() => { draftEpoch.current += 1; derivedImageDraftEpoch.current += 1; setNegativePrompt(''); }} className="text-primary">{feedback('clearNegativeForRequest')}</button></div>}
                            {strictFullBody && !qwenFullBody && !gptFullBodyT2I && applyStyle && !!effectiveStyle?.negative_prompt && <div className="text-amber-300"><p>{feedback('negativeUnsupported')}</p>
                                <button onClick={() => { draftEpoch.current += 1; derivedImageDraftEpoch.current += 1; setApplyStyle(false); }} className="text-primary">{feedback('disableStyleForRequest')}</button></div>}
                            {gptFullBodyT2I && applyStyle && !!effectiveStyle?.negative_prompt && <p role="status" className="text-text-secondary">{feedback('gptNegativeNotApplied')}</p>}
                            {preflightFailed === identity && <p role="status" className="text-amber-300">{feedback('toastRefreshFailed')}</p>}
                            <StudioImageRequestResults requests={requests} />
                        </div>}
                        generatingBatchSize={preparing?.identity === identity ? preparing.batchSize
                            : pendingFullBodyBatchSize || getGeneratingInfo("full_body").batchSize}
                        description="The primary reference for character consistency."
                        aspectRatio="9:16"

                        // Reverse generation: Show hint if upload detected but no full body
                        reverseGenerationMode={hasNonFullBodyUpload && !hasFullBodyImage}
                        reverseReferenceUrl={getUploadedReferenceUrl()}

                        supportsMotion={true}
                        mode={fullBodyMode}
                        onModeChange={setFullBodyMode}
                        hasStaticImage={!!asset.full_body_image_url || (asset.full_body_asset?.variants?.length > 0) || (asset.full_body?.image_variants?.length > 0)}
                        motionRefVideos={asset.full_body?.video_variants || []}
                        selectedMotionVideoId={asset.full_body?.selected_video_id}
                        onGenerateMotionRef={(prompt: string, audioUrl?: string) => handleGenerateMotionRef('full_body', prompt, audioUrl)}
                        isGeneratingMotion={generatingTypes.some(t => t.type === "video_full_body") || motionRequests.busy}
                        motionBlocked={motionRequests.blocked || isUploadingAudio}
                        motionRecovery={<><StudioMotionRequestResults requests={{ ...motionRequests,
                            results: motionRequests.results.filter(result => result.journal.request.asset_type === 'full_body') }} />
                            {motionPreflightFailed === motionIdentity && <p role="status" className="text-amber-300">{motionFeedback('sourceChanged')}</p>}</>}
                        motionPrompt={fullBodyMotionPrompt}
                        setMotionPrompt={(value: string) => { if (motionCurrent()) { if (value !== fullBodyMotionPrompt) motionDraftEpoch.current += 1; setFullBodyMotionPrompt(value); } }}
                        audioUrl={fullBodyAudioUrl}
                        onAudioUpload={(file: File) => handleAudioUpload(file, 'full_body')}
                        isUploadingAudio={isUploadingAudio}
                        isVideoLoading={isVideoLoading}
                        setIsVideoLoading={setIsVideoLoading}
                        onResetPrompt={() => handleResetMotionPrompt('full_body')}
                    />


                    {/* Divider */}
                    <div className="w-px bg-glass flex items-center justify-center">
                        <ChevronRight size={16} className="text-text-muted" />
                    </div>

                    {/* Panel 2: Three View (Derived) */}
                    <WorkbenchPanel
                        title={tc("threeViews")}
                        isActive={activePanel === "three_view"}
                        onClick={() => setActivePanel("three_view")}

                        asset={asset.three_view_asset}
                        currentImageUrl={asset.three_view_image_url}
                        onSelect={(id: string) => handleSelectVariant("three_view", id)}
                        onDelete={(id: string) => handleDeleteVariant("three_view", id)}
                        onFavorite={(id: string, isFav: boolean) => handleFavoriteVariant("three_view", id, isFav)}

                        prompt={threeViewPrompt}
                        setPrompt={(value: string) => {
                            if (value !== threeViewPrompt) { motionDraftEpoch.current += 1; derivedImageDraftEpoch.current += 1; }
                            setThreeViewPrompt(value);
                        }}
                        onGenerate={(batchSize: number) => handleGenerateClick("three_view", batchSize)}
                        onBatchSizeChange={strictThreeView ? () => {
                            if (!derivedImageCurrent()) return false;
                            derivedImageDraftEpoch.current += 1; return true;
                        } : undefined}
                        isGenerating={getGeneratingInfo("three_view").isGenerating || threeViewRequests.busy || threeViewPreparing?.identity === derivedImageIdentity}
                        generatingBatchSize={threeViewPreparing?.identity === derivedImageIdentity ? threeViewPreparing.batchSize
                            : pendingThreeViewBatchSize || getGeneratingInfo("three_view").batchSize}
                        generateDisabled={closing || coreThreeView && !qwenThreeView && !!negativePrompt || threeViewStyleNegativeBlocked
                            || strictThreeView && !threeViewReference
                            || !coreThreeView && legacyThreeViewBlocked}
                        requestResults={<div className="space-y-2 mt-2 text-xs">
                            {coreThreeView && !qwenThreeView && !!negativePrompt && <div className="text-amber-300"><p>{feedback('negativeDraftUnsupported')}</p>
                                <button onClick={() => { draftEpoch.current += 1; derivedImageDraftEpoch.current += 1; setNegativePrompt(''); }} className="text-primary">{feedback('clearNegativeForRequest')}</button></div>}
                            {strictThreeView && !threeViewReference && <p role="status" className="text-amber-300">{feedback('threeViewReferenceRequired')}</p>}
                            {!coreThreeView && legacyThreeViewBlocked && (threeViewRequests.results.length > 0 || threeViewRequests.readFailed)
                                && <p role="status" className="text-amber-300">{feedback('threeViewLegacyPending')}</p>}
                            {threeViewStyleNegativeBlocked && <p role="status" className="text-amber-300">{feedback('negativeUnsupported')}</p>}
                            {coreThreeView && !wanThreeView && !qwenThreeView && applyStyle && <p role="status" className="text-amber-300">{feedback('threeViewStyleNegativeNotApplied')}</p>}
                            {threeViewPreflightFailed === derivedImageIdentity && <p role="status" className="text-amber-300">{feedback('toastRefreshFailed')}</p>}
                            <StudioImageRequestResults requests={threeViewRequests} />
                        </div>}
                        isLocked={strictThreeView ? !threeViewReference : !asset.full_body_image_url && !hasAnyUpload}
                        description="Front, side, and back views for 3D-like consistency."
                        aspectRatio={coreThreeView ? "1:1" : "16:9"}
                    />

                    {/* Divider */}
                    <div className="w-px bg-glass flex items-center justify-center">
                        <ChevronRight size={16} className="text-text-muted" />
                    </div>

                    {/* Panel 3: Headshot (Derived) */}
                    <WorkbenchPanel
                        title={tc("avatar")}
                        isActive={activePanel === "headshot"}
                        onClick={() => setActivePanel("headshot")}

                        asset={asset.headshot_asset}
                        currentImageUrl={asset.headshot_image_url || asset.avatar_url}
                        onSelect={(id: string) => handleSelectVariant("headshot", id)}
                        onDelete={(id: string) => handleDeleteVariant("headshot", id)}
                        onFavorite={(id: string, isFav: boolean) => handleFavoriteVariant("headshot", id, isFav)}

                        prompt={headshotPrompt}
                        setPrompt={(value: string) => {
                            if (value !== headshotPrompt) { motionDraftEpoch.current += 1; derivedImageDraftEpoch.current += 1; }
                            setHeadshotPrompt(value);
                        }}
                        onGenerate={(batchSize: number) => handleGenerateClick("headshot", batchSize)}
                        onBatchSizeChange={strictHeadshot ? () => {
                            if (!derivedImageCurrent()) return false;
                            derivedImageDraftEpoch.current += 1; return true;
                        } : undefined}
                        isGenerating={getGeneratingInfo("headshot").isGenerating || headshotRequests.busy || headshotPreparing?.identity === derivedImageIdentity}
                        generatingBatchSize={headshotPreparing?.identity === derivedImageIdentity ? headshotPreparing.batchSize
                            : pendingHeadshotBatchSize || getGeneratingInfo("headshot").batchSize}
                        generateDisabled={closing || !headshotReferenceAvailable || coreHeadshot && !!negativePrompt
                            || !coreHeadshot && legacyHeadshotBlocked}
                        requestResults={<div className="space-y-2 mt-2 text-xs">
                            {coreHeadshot && !!negativePrompt && <div className="text-amber-300"><p>{feedback('negativeDraftUnsupported')}</p>
                                <button onClick={() => { draftEpoch.current += 1; derivedImageDraftEpoch.current += 1; setNegativePrompt(''); }} className="text-primary">{feedback('clearNegativeForRequest')}</button></div>}
                            {!headshotReferenceAvailable && <p role="status" className="text-amber-300">{feedback('headshotReferenceRequired')}</p>}
                            {!coreHeadshot && legacyHeadshotBlocked && (headshotRequests.results.length > 0 || headshotRequests.readFailed)
                                && <p role="status" className="text-amber-300">{feedback('headshotLegacyPending')}</p>}
                            {coreHeadshot && applyStyle && <p role="status" className="text-amber-300">{feedback('headshotStyleNegativeNotApplied')}</p>}
                            {headshotPreflightFailed === derivedImageIdentity && <p role="status" className="text-amber-300">{feedback('toastRefreshFailed')}</p>}
                            <StudioImageRequestResults requests={headshotRequests} />
                        </div>}
                        isLocked={!headshotReferenceAvailable}
                        description="Close-up facial details and expressions."
                        aspectRatio="1:1"

                        supportsMotion={true}
                        mode={headshotMode}
                        onModeChange={setHeadshotMode}
                        hasStaticImage={!!asset.headshot_image_url || (asset.headshot_asset?.variants?.length > 0) || (asset.head_shot?.image_variants?.length > 0)}
                        motionRefVideos={asset.head_shot?.video_variants || []}
                        selectedMotionVideoId={asset.head_shot?.selected_video_id}
                        onGenerateMotionRef={(prompt: string, audioUrl?: string) => handleGenerateMotionRef('head_shot', prompt, audioUrl)}
                        isGeneratingMotion={generatingTypes.some(t => t.type === "video_head_shot") || motionRequests.busy}
                        motionBlocked={motionRequests.blocked || isUploadingAudio}
                        motionRecovery={<><StudioMotionRequestResults requests={{ ...motionRequests,
                            results: motionRequests.results.filter(result => result.journal.request.asset_type === 'head_shot') }} />
                            {motionPreflightFailed === motionIdentity && <p role="status" className="text-amber-300">{motionFeedback('sourceChanged')}</p>}</>}
                        motionPrompt={headshotMotionPrompt}
                        setMotionPrompt={(value: string) => { if (motionCurrent()) { motionDraftEpoch.current += 1; setHeadshotMotionPrompt(value); } }}
                        audioUrl={headshotAudioUrl}
                        onAudioUpload={(file: File) => handleAudioUpload(file, 'head_shot')}
                        isUploadingAudio={isUploadingAudio}
                        isVideoLoading={isVideoLoading}
                        setIsVideoLoading={setIsVideoLoading}
                        onResetPrompt={() => handleResetMotionPrompt('headshot')}
                    />


                </div>

                {/* Footer: Negative Prompt & Art Direction Settings */}
                <div className="border-t border-glass-border bg-surface flex flex-col">
                    {/* Top Row: User's Negative Prompt + Apply Style Toggle */}
                    <div className="px-6 py-3 flex items-start gap-4">
                        {/* User's Negative Prompt (Editable) */}
                        <div className="flex-1">
                            <label className="text-xs font-bold text-text-muted uppercase mb-2 block">{tc("workbench")}</label>
                            <textarea
                                value={negativePrompt}
                                onChange={(e) => { draftEpoch.current += 1; derivedImageDraftEpoch.current += 1; setNegativePrompt(e.target.value); }}
                                className="w-full h-16 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono"
                                placeholder="Enter your negative prompt (avoid unwanted elements)..."
                            />
                        </div>

                        {/* Apply Style Toggle */}
                        <div className="pt-6">
                            <div className="flex items-center gap-2 bg-surface px-4 py-2 rounded-lg border border-glass-border">
                                <input
                                    type="checkbox"
                                    id="applyStyleFooter"
                                    checked={applyStyle}
                                    onChange={(e) => { draftEpoch.current += 1; derivedImageDraftEpoch.current += 1; setApplyStyle(e.target.checked); }}
                                    className="rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary w-4 h-4"
                                />
                                <label htmlFor="applyStyleFooter" className="text-xs font-bold text-text-secondary cursor-pointer select-none whitespace-nowrap">
                                    {tc("workbench")}
                                </label>
                            </div>
                        </div>
                    </div>

                    {/* Art Direction Style Display (Collapsible) - Only show toggle when style exists */}
                    {applyStyle && (stylePrompt || styleNegativePrompt) && (
                        <div className="border-t border-border-subtle">
                            <button
                                onClick={() => setShowStyleExpanded(!showStyleExpanded)}
                                className="w-full px-6 py-2 flex items-center justify-between hover:bg-glass transition-colors"
                            >
                                <div className="flex items-center gap-2">
                                    <div className="w-2 h-2 rounded-full bg-gradient-to-r from-purple-500 to-blue-500" />
                                    <span className="text-xs font-bold text-text-secondary uppercase">{(strictThreeView && activePanel === 'three_view' || coreHeadshot && activePanel === 'headshot') ? feedback('styleAppliedFrom') : 'Art Direction Style (Will Be Appended)'}</span>
                                </div>
                                <ChevronRight size={14} className={`text-text-muted transform transition-transform ${showStyleExpanded ? 'rotate-90' : ''}`} />
                            </button>

                            <AnimatePresence>
                                {showStyleExpanded && (
                                    <motion.div
                                        initial={{ height: 0, opacity: 0 }}
                                        animate={{ height: "auto", opacity: 1 }}
                                        exit={{ height: 0, opacity: 0 }}
                                        className="overflow-hidden"
                                    >
                                        <div className="px-6 pb-4">
                                            <div className="bg-gradient-to-r from-purple-500/10 to-blue-500/10 border border-glass-border rounded-lg p-4">
                                                {stylePrompt && (
                                                    <div className="mb-3">
                                                        <span className="text-xs font-bold text-green-400 block mb-1">+ Style Prompt:</span>
                                                        <p className="text-xs text-text-secondary font-mono bg-surface p-2 rounded border border-border-subtle leading-relaxed">
                                                            {stylePrompt}
                                                        </p>
                                                    </div>
                                                )}

                                                {styleNegativePrompt && (
                                                    <div>
                                                        <span className="text-xs font-bold text-red-400 block mb-1">{(strictThreeView && !wanThreeView && !qwenThreeView && activePanel === 'three_view' || coreHeadshot && activePanel === 'headshot' || gptFullBodyT2I && activePanel === 'full_body') ? feedback('gptNegativeNotApplied') : '+ Negative Prompt:'}</span>
                                                        <p className="text-xs text-text-secondary font-mono bg-surface p-2 rounded border border-border-subtle leading-relaxed">
                                                            {styleNegativePrompt}
                                                        </p>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>
                    )}
                </div>
            </motion.div>
        </div>
    );
}

function WorkbenchPanel({
    title,
    isActive,
    onClick,

    // Variant Props
    asset,
    currentImageUrl,
    onSelect,
    onDelete,
    onFavorite,

    prompt,
    setPrompt,
    onGenerate,
    onBatchSizeChange,
    isGenerating,
    generateDisabled,
    requestResults,
    generatingBatchSize,
    status,
    isLocked,
    description,
    aspectRatio = "9:16",
    // Video specific
    isVideo = false,
    videos,
    onDeleteVideo,
    onGenerateVideo,

    // Motion Ref Mode (Asset Activation v2)
    supportsMotion = false,
    mode = 'static',  // 'static' | 'motion'
    onModeChange,
    hasStaticImage = false,
    motionRefVideos = [],
    selectedMotionVideoId,
    onGenerateMotionRef,
    isGeneratingMotion = false,
    motionBlocked = false,
    motionRecovery,
    motionPrompt = '',
    setMotionPrompt,
    audioUrl = '',
    onAudioUpload,
    isUploadingAudio = false,
    isVideoLoading = false,
    setIsVideoLoading,
    onResetPrompt,
    // Reverse Generation Props
    reverseGenerationMode = false,
    reverseReferenceUrl = null
}: any) {
    const tc = useTranslations("character");
    const currentMotionVideo = selectedMotionVideoId == null ? motionRefVideos[motionRefVideos.length - 1]
        : motionRefVideos.find((variant: { id: string }) => variant.id === selectedMotionVideoId);

    return (
        <div
            role="group"
            aria-label={title}
            className={`flex-1 flex flex-col min-w-[300px] transition-colors ${isActive ? 'bg-glass' : 'bg-transparent hover:bg-hover-bg'}`}
            onClick={onClick}
        >
            {/* Panel Header */}
            <div className="p-4 border-b border-border-subtle">
                <div className="flex items-center justify-between mb-1">
                    <h3 className={`font-bold text-sm uppercase tracking-wider ${isActive ? 'text-primary' : 'text-text-secondary'}`}>
                        {title}
                    </h3>

                    {/* Mode Switcher (Asset Activation v2) */}
                    {supportsMotion && (
                        <div className="flex items-center gap-1 bg-surface p-1 rounded-lg border border-glass-border">
                            <button
                                onClick={(e) => { e.stopPropagation(); onModeChange?.('static'); }}
                                className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${mode === 'static'
                                    ? 'bg-primary/20 text-primary'
                                    : 'text-text-secondary hover:text-foreground'
                                    }`}
                            >
                                <PhotoIcon size={12} />
                                Static
                            </button>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation();
                                    if (!hasStaticImage) {
                                        alert(tc('generateFirstStatic', { type: tc('fullBodyType') }));
                                        return;
                                    }
                                    onModeChange?.('motion');
                                }}
                                className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${mode === 'motion'
                                    ? 'bg-purple-500/20 text-purple-400'
                                    : 'text-text-secondary hover:text-foreground'
                                    }`}
                            >
                                <Video size={12} />
                                {tc("motionMode")}
                            </button>
                        </div>
                    )}
                </div>
                <p className="text-xs text-text-muted">{description}</p>
            </div>

            {/* Image Area with Variant Selector */}
            <div className="flex-1 relative bg-surface p-4 flex flex-col overflow-y-auto group">

                {/* Locked Overlay */}
                {isLocked && (
                    <div className="absolute inset-0 bg-overlay z-20 flex items-center justify-center text-center p-6">
                        <div className="text-text-muted flex flex-col items-center gap-2">
                            <Lock size={32} />
                            <span className="text-sm">Generate Master Asset first</span>
                        </div>
                    </div>
                )}

                {/* Reverse Generation Hint - shown in Full Body panel when upload detected */}
                {reverseGenerationMode && (
                    <div className="absolute inset-0 bg-gradient-to-b from-primary/10 to-transparent z-10 flex flex-col items-center justify-center text-center p-6 pointer-events-none">
                        <div className="flex flex-col items-center gap-3 bg-overlay backdrop-blur-md rounded-xl p-6 border border-primary/30 pointer-events-auto">
                            <div className="flex items-center gap-2 text-primary">
                                <RefreshCw size={20} />
                                <span className="text-sm font-bold">Upload Detected</span>
                            </div>
                            <p className="text-xs text-text-secondary max-w-[200px]">
                                Generate Full Body from your uploaded reference image
                            </p>
                            {reverseReferenceUrl && (
                                <img
                                    src={typeof reverseReferenceUrl === 'string' && reverseReferenceUrl.startsWith('http')
                                        ? reverseReferenceUrl
                                        : `${window.location.origin}/${reverseReferenceUrl}`}
                                    alt="Reference"
                                    className="w-16 h-16 rounded-lg object-cover border border-glass-border"
                                />
                            )}
                        </div>
                    </div>
                )}

                {/* Variant Selector / Motion Ref Content */}
                <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin scrollbar-thumb-gray-700">
                    {mode === 'motion' && supportsMotion ? (
                        /* Motion Ref Mode Content - Matching Static Style */
                        <div className="flex flex-col gap-4 p-4">
                            {/* Header with gradient accent */}
                            <div className="flex items-center gap-2 pb-2 border-b border-purple-500/20">
                                <div className="w-1 h-4 bg-gradient-to-b from-purple-400 to-pink-500 rounded-full"></div>
                                <span className="text-xs font-bold text-purple-300 uppercase tracking-wider">{tc("motionRef")}</span>
                            </div>

                            {/* Video Player with glassmorphism */}
                            <div className={`relative w-full ${aspectRatio === '9:16' ? 'aspect-[9/16] max-h-[40vh]' : aspectRatio === '1:1' ? 'aspect-square max-h-[35vh]' : 'aspect-video'} bg-gradient-to-br from-overlay to-black/60 rounded-xl overflow-hidden border border-border-subtle shadow-xl backdrop-blur-sm`}>
                                {isGeneratingMotion ? (
                                    <div className="absolute inset-0 z-10 bg-overlay backdrop-blur-md flex flex-col items-center justify-center gap-4">
                                        <div className="relative">
                                            <RefreshCw size={48} className="text-purple-400 animate-spin" />
                                            <div className="absolute inset-0 blur-xl bg-purple-500/30 animate-pulse"></div>
                                        </div>
                                        <div className="flex flex-col items-center">
                                            <span className="text-sm font-bold text-foreground uppercase tracking-widest animate-pulse">Generating Video</span>
                                            <span className="text-[0.625rem] text-purple-300/60 mt-1">AI is processing motion...</span>
                                        </div>
                                    </div>
                                ) : isVideoLoading && currentMotionVideo?.url ? (
                                    <div className="absolute inset-0 z-10 bg-overlay backdrop-blur-sm flex flex-col items-center justify-center gap-3">
                                        <RefreshCw size={32} className="text-text-secondary animate-spin" />
                                        <span className="text-xs text-text-secondary font-medium">Loading Video File...</span>
                                    </div>
                                ) : null}

                                {currentMotionVideo?.url ? (
                                    <video
                                        key={currentMotionVideo.url}
                                        src={getAssetUrl(currentMotionVideo.url)}
                                        onCanPlay={() => setIsVideoLoading(false)}
                                        onLoadStart={() => setIsVideoLoading(true)}
                                        className="w-full h-full object-contain"
                                        controls
                                        loop
                                        autoPlay
                                        muted
                                    />
                                ) : !isGeneratingMotion && (
                                    <div className="w-full h-full flex flex-col items-center justify-center text-text-muted gap-2">
                                        <Video size={40} className="opacity-50" />
                                        <span className="text-sm">No motion reference yet</span>
                                        <span className="text-xs opacity-70">Generate one below</span>
                                    </div>
                                )}
                            </div>

                            <div className="bg-surface rounded-lg border border-glass-border p-3">
                                <label className="text-xs font-bold text-text-muted uppercase mb-2 block">Audio Input (Optional)</label>
                                <p className="text-xs text-text-muted mb-3">Upload audio to drive lip-sync or body rhythm</p>

                                <label className={`flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-dashed cursor-pointer transition-all ${audioUrl
                                    ? 'border-green-500/50 bg-green-500/10 text-green-400'
                                    : 'border-indigo-500/30 hover:border-indigo-400/50 hover:bg-indigo-500/5 text-text-secondary'
                                    }`}>
                                    <input
                                        type="file"
                                        accept="audio/*"
                                        className="hidden"
                                        onChange={(e) => {
                                            const file = e.target.files?.[0];
                                            if (file) onAudioUpload?.(file);
                                        }}
                                        disabled={isUploadingAudio}
                                    />
                                    {isUploadingAudio ? (
                                        <>
                                            <div className="animate-spin rounded-full h-4 w-4 border-2 border-primary/30 border-t-primary"></div>
                                            <span className="text-xs">Uploading...</span>
                                        </>
                                    ) : audioUrl ? (
                                        <>
                                            <Check size={14} />
                                            <span className="text-xs font-medium">Audio Uploaded</span>
                                        </>
                                    ) : (
                                        <>
                                            <ImageIcon size={14} />
                                            <span className="text-xs">Upload Audio File</span>
                                        </>
                                    )}
                                </label>
                            </div>

                            {/* Motion Prompt */}
                            <div className="flex flex-col gap-2">
                                <div className="flex items-center justify-between">
                                    <label className="text-xs font-bold text-text-muted uppercase">Motion Prompt</label>
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onResetPrompt?.();
                                        }}
                                        className="text-[0.625rem] text-primary hover:text-primary/80 transition-colors flex items-center gap-1"
                                        title="Reset to recommended prompt"
                                    >
                                        <RefreshCw size={10} />
                                        Reset
                                    </button>
                                </div>
                                <textarea
                                    value={motionPrompt}
                                    onChange={(e) => setMotionPrompt?.(e.target.value)}
                                    className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                    placeholder="Describe the motion you want..."
                                />
                            </div>

                            {/* Generate Button */}
                            <button
                                onClick={() => onGenerateMotionRef?.(motionPrompt, audioUrl)}
                                disabled={isGeneratingMotion || motionBlocked}
                                className={`flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${isGeneratingMotion
                                    ? 'bg-gray-700 text-text-muted cursor-not-allowed'
                                    : 'bg-primary hover:bg-primary/90 text-foreground shadow-lg'
                                    }`}
                            >
                                <Video size={16} />
                                Generate Motion Reference
                            </button>
                            {motionRecovery}
                        </div>
                    ) : isVideo ? (
                        <VideoVariantSelector
                            videos={videos}
                            onDelete={onDeleteVideo}
                            onGenerate={onGenerateVideo}
                            isGenerating={isGenerating}
                            aspectRatio={aspectRatio}
                            className="h-full"
                        />
                    ) : (
                        <VariantSelector
                            asset={asset}
                            currentImageUrl={currentImageUrl}
                            onSelect={onSelect}
                            onDelete={onDelete}
                            onFavorite={onFavorite}
                            onGenerate={onGenerate}
                            onBatchSizeChange={onBatchSizeChange}
                            isGenerating={isGenerating}
                            generateDisabled={generateDisabled}
                            generatingBatchSize={generatingBatchSize}
                            aspectRatio={aspectRatio}
                            className="h-full"
                        />
                    )}
                    {mode === 'static' && requestResults}
                </div>

                {/* Status Overlay (if outdated) */}
                {status === "outdated" && !isGenerating && (
                    <div className="absolute top-4 right-4 z-10">
                        <div className="bg-yellow-500/20 border border-yellow-500/50 px-3 py-1 rounded-lg flex items-center gap-2 backdrop-blur-sm">
                            <RefreshCw size={12} className="text-yellow-500" />
                            <span className="text-xs font-bold text-yellow-500">Update Recommended</span>
                        </div>
                    </div>
                )}
            </div>

            {/* Prompt Editor (Bottom) */}
            <div className="h-1/3 border-t border-glass-border flex flex-col bg-surface">
                <div className="p-2 border-b border-border-subtle flex justify-between items-center bg-surface">
                    <span className="text-xs font-bold text-text-muted uppercase px-2">Prompt</span>
                </div>
                <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    disabled={isLocked}
                    className="flex-1 w-full bg-transparent p-4 text-xs text-text-secondary resize-none focus:outline-none focus:bg-glass font-mono leading-relaxed"
                    placeholder="Enter prompt description..."
                />
            </div>
        </div>
    );
}
