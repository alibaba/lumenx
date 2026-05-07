import { API_URL } from "./api";
import { clsx, type ClassValue } from "clsx";
import { messages } from "./i18n";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export function getAssetUrl(path: string | null | undefined): string {
    if (!path) return "";
    if (path.startsWith("http") || path.startsWith("https") || path.startsWith("blob:")) return path;

    // Remove leading slash if present to avoid double slashes with API_URL/files/
    const cleanPath = path.startsWith("/") ? path.slice(1) : path;
    return `${API_URL}/files/${cleanPath}`;
}

export function isDirectAssetPath(path: string | null | undefined): boolean {
    if (!path) return false;
    return path.startsWith("http://") || path.startsWith("https://") || path.startsWith("blob:");
}

const PRESIGNED_QUERY_PREFIXES = ["x-tos-", "x-amz-", "x-goog-"];
const PRESIGNED_QUERY_KEYS = [
    "signature",
    "ossaccesskeyid",
    "x-tos-algorithm",
    "x-tos-signature",
    "x-amz-algorithm",
    "x-amz-signature",
    "x-goog-algorithm",
    "x-goog-signature",
];

export function isPresignedAssetUrl(path: string | null | undefined): boolean {
    const url = getAssetUrl(path);
    if (!url || !(url.startsWith("http://") || url.startsWith("https://"))) return false;

    try {
        const searchKeys = Array.from(new URL(url).searchParams.keys()).map((key) => key.toLowerCase());
        return searchKeys.some((key) =>
            PRESIGNED_QUERY_KEYS.includes(key)
            || PRESIGNED_QUERY_PREFIXES.some((prefix) => key.startsWith(prefix))
        );
    } catch {
        const lowerUrl = url.toLowerCase();
        return PRESIGNED_QUERY_KEYS.some((key) => lowerUrl.includes(`${key}=`));
    }
}

export function canAppendAssetQueryParams(path: string | null | undefined): boolean {
    const url = getAssetUrl(path);
    if (!url || url.startsWith("blob:")) return false;
    return !isPresignedAssetUrl(url);
}

export function appendAssetQueryParam(path: string | null | undefined, key: string, value: string | number): string {
    const url = getAssetUrl(path);
    if (!url) return "";
    if (!canAppendAssetQueryParams(url)) return url;

    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`;
}

export function getAssetUrlWithTimestamp(path: string | null | undefined, timestamp?: number): string {
    return appendAssetQueryParam(path, "t", timestamp || 0);
}

export function getAssetFetchUrl(path: string | null | undefined): string {
    if (!path) return "";
    if (isDirectAssetPath(path)) return getAssetUrl(path);

    if (process.env.NODE_ENV === "development") {
        const cleanPath = path.startsWith("/") ? path.slice(1) : path;
        const proxiedPath = cleanPath.startsWith("files/") ? cleanPath : `files/${cleanPath}`;
        return `/api-proxy/${proxiedPath}`;
    }

    return getAssetUrl(path);
}

export function stripAssetApiPrefix(path: string | null | undefined): string {
    if (!path) return "";

    const apiPrefix = `${API_URL}/files/`;
    if (path.startsWith(apiPrefix)) {
        return path.slice(apiPrefix.length);
    }

    const originPrefix = typeof window !== "undefined" ? `${window.location.origin}/files/` : "";
    if (originPrefix && path.startsWith(originPrefix)) {
        return path.slice(originPrefix.length);
    }

    if (path.startsWith("/files/")) {
        return path.slice("/files/".length);
    }

    if (path.startsWith("files/")) {
        return path.slice("files/".length);
    }

    return path;
}

export function extractErrorDetail(error: any, fallback: string = messages.common.messages.unknownError): string {
    return error?.response?.data?.detail
        || error?.response?.data?.message
        || error?.message
        || fallback;
}
