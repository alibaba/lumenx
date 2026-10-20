import { API_URL } from "./api";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export function getAssetUrl(path: string | null | undefined): string {
    if (!path) return "";
    if (path.startsWith("http") || path.startsWith("blob:")) {
        // Only pass through well-formed http(s)/blob URLs; anything else
        // (e.g. javascript: smuggled behind a weird prefix) is dropped.
        try {
            const protocol = new URL(path).protocol;
            if (protocol === "http:" || protocol === "https:" || protocol === "blob:") {
                // Strip HTML metacharacters as well; well-formed URLs never
                // contain them raw, so this is a no-op for legitimate values.
                return path.replace(/[<>"'`]/g, "");
            }
        } catch {
            // malformed URL — fall through to reject
        }
        return "";
    }

    // Remove leading slash if present to avoid double slashes with API_URL/files/
    const cleanPath = path.startsWith("/") ? path.slice(1) : path;
    return `${API_URL}/files/${encodeURI(cleanPath)}`;
}

export function getAssetUrlWithTimestamp(path: string | null | undefined, timestamp?: number): string {
    const baseUrl = getAssetUrl(path);
    if (!baseUrl) return "";

    // If URL already has query params, append with & otherwise with ?
    const separator = baseUrl.includes('?') ? '&' : '?';
    return baseUrl + separator + `t=${timestamp || 0}`;
}

export function extractErrorDetail(error: any, fallback = "未知错误"): string {
    return error?.response?.data?.detail
        || error?.response?.data?.message
        || error?.message
        || fallback;
}
