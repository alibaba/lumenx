import type { AtelierOrderedMediaRole } from "@/lib/atelierTypes";

export const ATELIER_ORDERED_MEDIA_ROLES: readonly AtelierOrderedMediaRole[] = [
    "first_frame",
    "last_frame",
    "reference_image",
    "reference_video",
    "reference_audio",
] as const;

const ROLE_ORDER = new Map<AtelierOrderedMediaRole, number>(ATELIER_ORDERED_MEDIA_ROLES.map((role, index) => [role, index]));

export function isAtelierOrderedMediaRole(value: unknown): value is AtelierOrderedMediaRole {
    return typeof value === "string" && ROLE_ORDER.has(value as AtelierOrderedMediaRole);
}

export function hasCanonicalAtelierOrderedMediaRoleOrder(media: readonly { role: AtelierOrderedMediaRole }[]): boolean {
    let previous = -1;
    for (const item of media) {
        const rank = ROLE_ORDER.get(item.role);
        if (rank === undefined || rank < previous) return false;
        previous = rank;
    }
    return true;
}
