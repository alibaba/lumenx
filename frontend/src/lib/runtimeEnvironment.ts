import { API_URL } from "@/lib/apiUrl";

export type RuntimeStorageIdentity = {
    schema_version: 1;
    storage_id: string;
};

export type RuntimeEnvironment = {
    apiUrl: string;
    storageId: string;
    key: string;
    generation: number;
};

type Listener = () => void;

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

let current: RuntimeEnvironment | null = null;
let generation = 0;
const listeners = new Set<Listener>();

const notify = () => listeners.forEach(listener => listener());

const isRecord = (value: unknown): value is Record<string, unknown> => (
    typeof value === "object" && value !== null && !Array.isArray(value)
);

export class RuntimeEnvironmentError extends Error {
    constructor(message: string, public readonly code = "runtime_environment_error") {
        super(message);
        this.name = "RuntimeEnvironmentError";
    }
}

export function validateRuntimeStorageIdentity(value: unknown): RuntimeStorageIdentity {
    if (!isRecord(value) || value.schema_version !== 1 || typeof value.storage_id !== "string"
        || !UUID_V4.test(value.storage_id)) {
        throw new RuntimeEnvironmentError("Invalid runtime storage identity", "invalid_runtime_environment");
    }
    return { schema_version: 1, storage_id: value.storage_id };
}

export function runtimeEnvironmentKey(apiUrl: string, storageId: string): string {
    return JSON.stringify([apiUrl, storageId]);
}

export function activateRuntimeEnvironment(identity: RuntimeStorageIdentity): RuntimeEnvironment {
    const validated = validateRuntimeStorageIdentity(identity);
    const key = runtimeEnvironmentKey(API_URL, validated.storage_id);
    if (current?.key === key) return current;
    generation += 1;
    current = {
        apiUrl: API_URL,
        storageId: validated.storage_id,
        key,
        generation,
    };
    notify();
    return current;
}

export function clearRuntimeEnvironment(): void {
    if (!current) return;
    generation += 1;
    current = null;
    notify();
}

export function getRuntimeEnvironment(): RuntimeEnvironment | null {
    return current;
}

export function requireActiveRuntimeEnvironment(): RuntimeEnvironment {
    if (!current) {
        throw new RuntimeEnvironmentError("Runtime environment is not active", "runtime_environment_unavailable");
    }
    return current;
}

export function isCurrentRuntimeEnvironment(key: string, capturedGeneration?: number): boolean {
    return !!current && current.key === key
        && (capturedGeneration === undefined || current.generation === capturedGeneration);
}

export function environmentLocalStorageKey(baseKey: string, key = requireActiveRuntimeEnvironment().key): string {
    return `${baseKey}:${key}`;
}

export function subscribeRuntimeEnvironment(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

export async function readRuntimeEnvironment(options: { signal?: AbortSignal } = {}): Promise<RuntimeStorageIdentity> {
    const response = await fetch(`${API_URL}/runtime/environment`, {
        method: "GET",
        credentials: "include",
        headers: { "Accept": "application/json" },
        cache: "no-store",
        redirect: "error",
        signal: options.signal,
    });
    if (!response.ok) {
        throw new RuntimeEnvironmentError("Unable to read runtime environment", "runtime_environment_read_failed");
    }
    const body = await response.json();
    return validateRuntimeStorageIdentity(body);
}
