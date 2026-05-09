import type { AtelierNode } from "@/lib/api";

export type AtelierCanvasNodeData = {
    reference_image_urls?: string[];
    reference_node_ids?: string[];
    parent_node_id?: string;
};

export type AtelierCanvasPoint = {
    x: number;
    y: number;
};

export type AtelierReferenceLink = {
    key: string;
    from: AtelierNode;
    to: AtelierNode;
    url: string;
};

export function getAtelierCanvasNodeData(node: AtelierNode): AtelierCanvasNodeData {
    return node.data as AtelierCanvasNodeData;
}

export function getAtelierReferenceNodeIds(node: AtelierNode): string[] {
    const refs = getAtelierCanvasNodeData(node).reference_node_ids;
    return Array.isArray(refs) ? refs : [];
}

export function isPointInsideAtelierNode(point: AtelierCanvasPoint, node: AtelierNode): boolean {
    return point.x >= node.x &&
        point.x <= node.x + node.width &&
        point.y >= node.y &&
        point.y <= node.y + node.height;
}

export function findVideoDropTarget(nodes: AtelierNode[], point: AtelierCanvasPoint): AtelierNode | null {
    return [...nodes].reverse().find((node) => node.type === "video" && isPointInsideAtelierNode(point, node)) ?? null;
}

export function buildReferenceLinks(nodes: AtelierNode[]): AtelierReferenceLink[] {
    const links = new Map<string, AtelierReferenceLink>();
    const addLink = (imageNode: AtelierNode, videoNode: AtelierNode) => {
        const url = imageNode.media_urls[0];
        if (!url) return;
        const key = `${imageNode.id}-${videoNode.id}`;
        links.set(key, { key, from: imageNode, to: videoNode, url });
    };

    nodes.forEach((node) => {
        if (node.type !== "image") return;
        const parentNodeId = getAtelierCanvasNodeData(node).parent_node_id;
        if (!parentNodeId) return;
        const parent = nodes.find((candidate) => candidate.id === parentNodeId && candidate.type === "video");
        if (parent) addLink(node, parent);
    });

    nodes.forEach((node) => {
        if (node.type !== "video") return;
        getAtelierReferenceNodeIds(node).forEach((referenceNodeId) => {
            const imageNode = nodes.find((candidate) => candidate.id === referenceNodeId && candidate.type === "image");
            if (imageNode) addLink(imageNode, node);
        });
    });

    return Array.from(links.values());
}
