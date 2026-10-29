/** Only live in-memory owners can make a persisted legacy busy marker authoritative. */
const owners = new WeakMap<object, () => boolean>();

export function trackAssetGeneration(marker: object, isCurrent: () => boolean): () => void {
    owners.set(marker, isCurrent);
    return () => { if (owners.get(marker) === isCurrent) owners.delete(marker); };
}

export function isAssetGenerationActive(marker: object): boolean {
    return owners.get(marker)?.() ?? false;
}
