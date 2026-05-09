import { describe, expect, it } from 'vitest';
import type { AtelierNode } from '@/lib/api';
import { buildReferenceLinks, findVideoDropTarget, isPointInsideAtelierNode } from '@/lib/atelierCanvas';

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
