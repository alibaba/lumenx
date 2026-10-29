import { API_URL } from "./apiUrl";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

/** Only inert inline raster types supported by Core may bypass the file route.
 * Header agreement is a display guard, not full image or provider validation. */
function isInlineRasterUrl(path: string): boolean {
    const match = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$/.exec(path);
    if (!match || match[0] !== path || match[2].length % 4 !== 0) return false;
    try {
        // Read only the fixed-size signature; do not decode an entire image per render.
        const header = atob(match[2].slice(0, 16));
        if (match[1] === "png") return header.startsWith("\x89PNG\r\n\x1a\n");
        if (match[1] === "jpeg") return header.startsWith("\xff\xd8\xff");
        return header.startsWith("RIFF") && header.slice(8, 12) === "WEBP";
    } catch {
        return false;
    }
}

export function getAssetUrl(path: string | null | undefined): string {
    if (!path) return "";
    if (/^\s*data:/i.test(path)) {
        // Encode at the URL output boundary as well as checking the raster contract.
        // All permitted MIME/base64 characters are unchanged by encodeURI.
        return isInlineRasterUrl(path) ? encodeURI(path) : "";
    }
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
    if (baseUrl.startsWith("data:")) return baseUrl;

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
