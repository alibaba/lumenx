/** Byte/storage primitives only. Request domains own identities, schemas and permissions. */
export const isPlainJournalData = (value: unknown): value is Record<string, unknown> => !!value
    && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));

export const serializeJournalData = (value: unknown): string => JSON.stringify(value, (_key, item) => isPlainJournalData(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);

export function assertJournalDataFields(value: unknown, reject: () => never): void {
    if (!isPlainJournalData(value)) reject();
    const descriptors = Object.getOwnPropertyDescriptors(value);
    for (const name of Reflect.ownKeys(descriptors)) {
        const descriptor = descriptors[name as string];
        if (typeof name !== 'string' || !descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) reject();
    }
}

export function resolveJournalStorage(options: { storage?: Storage }, reject: () => never): Storage {
    try { return options.storage ?? window.localStorage; }
    catch { return reject(); }
}

export function listJournalRecords<T>(storage: Storage, prefix: string, decode: (raw: string | null, key: string) => T): T[] {
    const result: T[] = [];
    for (let i = 0; i < storage.length; i++) {
        const key = storage.key(i);
        if (key?.startsWith(prefix)) result.push(decode(storage.getItem(key), key));
    }
    return result;
}

export function writeJournalBytes(storage: Storage, key: string, raw: string, reject: () => never): void {
    try { storage.setItem(key, raw); if (storage.getItem(key) !== raw) reject(); }
    catch { reject(); }
}
