export type LibraryAssetType = "character" | "scene" | "prop";
export type LibraryAssetCollection = "characters" | "scenes" | "props";

const COLLECTION_BY_TYPE: Record<LibraryAssetType, LibraryAssetCollection> = {
    character: "characters",
    scene: "scenes",
    prop: "props",
};

export function libraryRevision(value: unknown): number | null {
    const revision = (value as { revision?: unknown } | null)?.revision;
    return typeof revision === "number" && Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

export function libraryHasAsset(value: unknown, assetType: LibraryAssetType, assetId: string): boolean {
    const collection = (value as Record<LibraryAssetCollection, unknown> | null)?.[COLLECTION_BY_TYPE[assetType]];
    return Array.isArray(collection) && collection.some((asset) => (asset as { id?: unknown } | null)?.id === assetId);
}

export function libraryOperationKey(kind: "create" | "promote" | "generate", apiUrl: string, parts: Array<string | null | undefined>): string {
    return `library-${kind}:${encodeURIComponent(apiUrl)}:${parts.map((part) => encodeURIComponent(part ?? "")).join(":")}`;
}

export type LibraryRecoveryRecord =
    | { kind: "create"; key: string; assetType: LibraryAssetType; assetId: string; name: string }
    | { kind: "promote"; key: string; assetType: LibraryAssetType; assetId: string; name: string }
    | { kind: "variant"; key: string; projectId: string; assetId: string; assetType: LibraryAssetType; taskId?: string; beforeVariantIds: string[] };

const recoveryRecords = new Map<string, LibraryRecoveryRecord>();
const recoveryListeners = new Set<() => void>();
let recoveryVersion = 0;

function emitRecoveryChange(): void {
    recoveryVersion += 1;
    recoveryListeners.forEach((listener) => listener());
}

export function subscribeLibraryRecoveries(listener: () => void): () => void {
    recoveryListeners.add(listener);
    return () => recoveryListeners.delete(listener);
}

export function getLibraryRecoveryVersion(): number {
    return recoveryVersion;
}

export function rememberLibraryRecovery(record: LibraryRecoveryRecord): void {
    recoveryRecords.set(record.key, record);
    emitRecoveryChange();
}

export function forgetLibraryRecovery(key: string): void {
    if (recoveryRecords.delete(key)) emitRecoveryChange();
}

export function findLibraryRecovery(predicate: (record: LibraryRecoveryRecord) => boolean): LibraryRecoveryRecord | undefined {
    return Array.from(recoveryRecords.values()).find(predicate);
}

export function clearLibraryRecoveries(): void {
    if (recoveryRecords.size === 0) return;
    recoveryRecords.clear();
    emitRecoveryChange();
}
