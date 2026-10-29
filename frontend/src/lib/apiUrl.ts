const BACKEND_PORT = process.env.NEXT_PUBLIC_BACKEND_PORT || "17177";

export const getApiUrl = (): string => {
    const override = process.env.NEXT_PUBLIC_API_URL;
    if (override && override.trim()) {
        return override.trim().replace(/\/+$/, "");
    }

    if (typeof window !== "undefined") {
        const { protocol, hostname, port } = window.location;

        if (protocol === "tauri:" || protocol === "https:" && hostname === "tauri.localhost") {
            return `http://127.0.0.1:${BACKEND_PORT}`;
        }

        if (process.env.NODE_ENV === "development") {
            return `${protocol}//${hostname}:${BACKEND_PORT}`;
        }

        return `${protocol}//${hostname}${port ? ":" + port : ""}`;
    }

    return `http://localhost:${BACKEND_PORT}`;
};

export const API_URL = getApiUrl();
