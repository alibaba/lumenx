export const PROJECT_PACKAGE_MAX_BYTES = 256 * 1024 * 1024;

export interface ProjectPackageReceipt {
    id: string;
    revision: number;
    title: string;
}

export function parseProjectPackageReceipt(value: unknown): ProjectPackageReceipt {
    const raw = value as { id?: unknown; title?: unknown; revision?: unknown } | null;
    if (!raw || typeof raw.id !== "string" || !raw.id.trim()
        || typeof raw.title !== "string"
        || typeof raw.revision !== "number" || !Number.isSafeInteger(raw.revision) || raw.revision < 0) {
        throw new Error("Invalid imported project acknowledgement");
    }
    return { id: raw.id, revision: raw.revision, title: raw.title };
}

export function safeProjectPackageFilename(title: string, id: string, fallback?: string): string {
    const fallbackName = fallback?.trim();
    if (fallbackName && /^[A-Za-z0-9._ -]+\.zip$/i.test(fallbackName) && !fallbackName.includes("..")) {
        return fallbackName;
    }
    const base = title.trim()
        .replace(/[\\/:*?"<>|]+/g, "-")
        .replace(/\s+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80) || "lumenx-project";
    const safeId = id.trim().replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 48) || "project";
    return `${base}-${safeId}.lumenx.zip`;
}

export function filenameFromContentDisposition(value: string | null): string | undefined {
    if (!value) return undefined;
    const utf8 = value.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8?.[1]) {
        try { return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, "")); }
        catch { return utf8[1].trim().replace(/^"|"$/g, ""); }
    }
    const ascii = value.match(/filename="?([^";]+)"?/i);
    return ascii?.[1]?.trim();
}
