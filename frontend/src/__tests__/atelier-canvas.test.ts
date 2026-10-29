import { describe, expect, it } from 'vitest';
import type { AtelierNode, JsonRecord } from '@/lib/atelierTypes';
import { buildReferenceLinks, canonicalAtelierReferenceData, findVideoDropTarget, getAtelierPendingReferenceNodes, getAtelierReferenceMediaUrls, isPointInsideAtelierNode, resolveAtelierReferenceMedia } from '@/lib/atelierCanvas';
import referenceContractCases from './fixtures/atelier-reference-contract.json';
import referenceResolutionCases from './fixtures/atelier-reference-resolution.json';


type ReferenceContractNode = {
    id: string;
    type: string;
    project_id?: string;
    media_urls?: string[];
    data?: JsonRecord;
};

type ReferenceContractCase = {
    name: string;
    target_id: string;
    nodes: ReferenceContractNode[];
    expected: Record<string, string[]>;
};

const referenceContract = referenceContractCases as ReferenceContractCase[];
const referenceResolution = referenceResolutionCases as unknown as { name: string; data: JsonRecord; expected: string | null }[];

function makeContractNode(node: ReferenceContractNode): AtelierNode {
    return makeNode({
        id: node.id,
        type: node.type,
        project_id: node.project_id ?? 'p1',
        media_urls: node.media_urls ?? [],
        data: node.data ?? {},
    });
}

function makeNode(overrides: Partial<AtelierNode>): AtelierNode {
    return {
        id: 'node',
        project_id: 'atelier-1',
        type: 'video',
        title: 'Node',
        prompt: '',
        status: 'draft',
        x: 0,
        y: 0,
        width: 100,
        height: 100,
        source_project_id: null,
        frame_id: null,
        asset_id: null,
        video_task_id: null,
        media_urls: [],
        data: {},
        created_by: 'user',
        created_at: 1,
        updated_at: 1,
        ...overrides,
    };
}

describe('atelier canvas geometry', () => {
    it('detects points inside node bounds inclusively', () => {
        const node = makeNode({ x: 20, y: 30, width: 120, height: 80 });

        expect(isPointInsideAtelierNode({ x: 20, y: 30 }, node)).toBe(true);
        expect(isPointInsideAtelierNode({ x: 140, y: 110 }, node)).toBe(true);
        expect(isPointInsideAtelierNode({ x: 141, y: 110 }, node)).toBe(false);
    });

    it('finds the visually topmost video target when nodes overlap', () => {
        const bottomVideo = makeNode({ id: 'video-bottom', x: 40, y: 40, width: 180, height: 160 });
        const topVideo = makeNode({ id: 'video-top', x: 80, y: 80, width: 180, height: 160 });
        const image = makeNode({ id: 'image-1', type: 'image', x: 100, y: 100, width: 80, height: 80 });

        const target = findVideoDropTarget([bottomVideo, image, topVideo], { x: 100, y: 100 });

        expect(target?.id).toBe('video-top');
    });

    it('ignores image nodes and returns null outside video bounds', () => {
        const image = makeNode({ id: 'image-1', type: 'image', x: 0, y: 0, width: 200, height: 200 });
        const video = makeNode({ id: 'video-1', x: 300, y: 300, width: 120, height: 120 });

        expect(findVideoDropTarget([image, video], { x: 50, y: 50 })).toBeNull();
    });
});

describe('atelier reference links', () => {

    it.each(referenceResolution)('resolves the same take as the service: $name', ({ data, expected }) => {
        expect(resolveAtelierReferenceMedia(makeNode({ type: 'video', media_urls: ['video/node.mp4'], data }))).toBe(expected);
    });

    it.each(referenceContract)('matches shared reference contract: $name', ({ target_id, nodes, expected }) => {
        const rawNodes = structuredClone(nodes);
        const typedNodes = nodes.map(makeContractNode);
        const target = typedNodes.find((node) => node.id === target_id);

        expect(target).toBeDefined();
        expect(canonicalAtelierReferenceData(target!, typedNodes)).toEqual(expected);
        expect(nodes).toEqual(rawNodes);
    });



    it('preserves pending reference identity without promoting it into media URL buckets', () => {
        const source = makeNode({ id: 'image-empty', type: 'image', media_urls: [] });
        const target = makeNode({ id: 'video-target', type: 'video', data: {
            reference_node_ids: ['image-empty'],
            pending_reference_nodes: [
                { schema_version: 1, source_node_id: 'image-empty', source_kind: 'image', status: 'pending_media' },
                { schema_version: 1, source_node_id: '', source_kind: 'image', status: 'pending_media' },
                { schema_version: 1, source_node_id: 'audio-empty', source_kind: 'text', status: 'pending_media' },
            ],
        } });

        expect(getAtelierPendingReferenceNodes(target)).toEqual([
            { schema_version: 1, source_node_id: 'image-empty', source_kind: 'image', status: 'pending_media' },
        ]);
        expect(canonicalAtelierReferenceData(target, [target, source])).toEqual({
            reference_node_ids: ['image-empty'],
            pending_reference_nodes: [{ schema_version: 1, source_node_id: 'image-empty', source_kind: 'image', status: 'pending_media' }],
        });
        expect(buildReferenceLinks([source, target])).toEqual([]);
    });

    it('resolves completed candidate media_url and legacy video_url parity', () => {
        const current = makeNode({ id: 'current', type: 'video', media_urls: ['video/fallback.mp4'], data: {
            selected_candidate_id: 'selected',
            candidates: [
                { id: 'other', status: 'completed', media_url: 'video/other.mp4' },
                { id: 'selected', status: 'completed', media_url: 'video/current.mp4' },
            ],
        } });
        const legacy = makeNode({ id: 'legacy', type: 'video', data: {
            selected_candidate_id: 'legacy-selected',
            candidates: [
                { id: 'legacy-selected', status: 'completed', video_url: 'video/legacy.mp4' },
            ],
        } });

        expect(resolveAtelierReferenceMedia(current)).toBe('video/current.mp4');
        expect(resolveAtelierReferenceMedia(legacy)).toBe('video/legacy.mp4');
        expect(getAtelierReferenceMediaUrls(current)).toEqual(['video/fallback.mp4', 'video/other.mp4', 'video/current.mp4']);
    });


    it('preserves explicit empty reference URL buckets for clearing semantics', () => {
        const target = makeNode({ id: 'video-clear', type: 'video', data: {
            reference_image_urls: [],
            reference_video_urls: [],
            reference_audio_urls: ['audio/ref.wav'],
        } });

        expect(canonicalAtelierReferenceData(target, [target])).toEqual({
            reference_image_urls: [],
            reference_video_urls: [],
            reference_audio_urls: ['audio/ref.wav'],
            reference_node_ids: [],
        });
    });

    it('does not fall back to stale media_urls when raw candidate entries exist without completed records', () => {
        const node = makeNode({ id: 'stale', type: 'video', media_urls: ['video/stale.mp4'], data: {
            candidates: [null, 'bad'],
        } });

        expect(resolveAtelierReferenceMedia(node)).toBeNull();
    });

    it('keeps both media_url and legacy video_url aliases from completed candidates', () => {
        const node = makeNode({ id: 'aliases', type: 'video', media_urls: ['video/upload.mp4'], data: {
            candidates: [
                { id: 'active', status: 'completed', media_url: 'video/active-current.mp4', video_url: 'video/active-legacy.mp4' },
            ],
            deleted_candidates: [
                { id: 'deleted', status: 'completed', media_url: 'video/deleted-current.mp4', video_url: 'video/deleted-legacy.mp4' },
                { id: 'duplicate', status: 'completed', media_url: 'video/active-current.mp4', video_url: 'video/deleted-legacy.mp4' },
            ],
        } });

        expect(getAtelierReferenceMediaUrls(node)).toEqual([
            'video/upload.mp4',
            'video/active-current.mp4',
            'video/active-legacy.mp4',
            'video/deleted-current.mp4',
            'video/deleted-legacy.mp4',
        ]);
    });

    it('keeps the bound media URL and marks replaced source media stale', () => {
        const source = makeNode({ id: 'image', type: 'image', media_urls: ['uploads/new.png'], data: {
            candidates: [
                { id: 'old', status: 'completed', media_kind: 'image', media_url: 'uploads/old.png' },
                { id: 'new', status: 'completed', media_kind: 'image', media_url: 'uploads/new.png' },
            ],
        } });
        const target = makeNode({ id: 'video', data: { reference_node_ids: ['image'], reference_image_urls: ['uploads/old.png'] } });
        expect(buildReferenceLinks([source, target])[0]).toMatchObject({ url: 'uploads/old.png', stale: true });
    });
    it('builds links from both image parent data and video reference ids', () => {
        const video = makeNode({
            id: 'video-1',
            type: 'video',
            data: {
                reference_node_ids: ['image-1'],
            },
        });
        const image = makeNode({
            id: 'image-1',
            type: 'image',
            media_urls: ['uploads/ref-a.png'],
            data: {
                parent_node_id: 'video-1',
            },
        });

        const links = buildReferenceLinks([video, image]);

        expect(links).toHaveLength(1);
        expect(links[0]).toMatchObject({
            key: 'image-1-video-1',
            url: 'uploads/ref-a.png',
        });
        expect(links[0].from.id).toBe('image-1');
        expect(links[0].to.id).toBe('video-1');
    });

    it('dedupes one image-video pair reached through repeated reference paths', () => {
        const video = makeNode({
            id: 'video-1',
            type: 'video',
            data: {
                reference_node_ids: ['image-1', 'image-1'],
            },
        });
        const image = makeNode({
            id: 'image-1',
            type: 'image',
            media_urls: ['uploads/ref-a.png'],
            data: {
                parent_node_id: 'video-1',
            },
        });

        const links = buildReferenceLinks([image, video]);

        expect(links.map((link) => link.key)).toEqual(['image-1-video-1']);
    });

    it('skips orphaned and media-less reference records', () => {
        const video = makeNode({
            id: 'video-1',
            data: {
                reference_node_ids: ['missing-image', 'image-empty'],
            },
        });
        const orphanedImage = makeNode({
            id: 'image-orphaned',
            type: 'image',
            media_urls: ['uploads/orphaned.png'],
            data: {
                parent_node_id: 'missing-video',
            },
        });
        const mediaLessImage = makeNode({
            id: 'image-empty',
            type: 'image',
            media_urls: [],
            data: {
                parent_node_id: 'video-1',
            },
        });

        expect(buildReferenceLinks([video, orphanedImage, mediaLessImage])).toEqual([]);
    });
});
