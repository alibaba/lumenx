export type PersistenceFailure = 'conflict' | 'unavailable' | 'failed' | 'missingRevision';

export function persistenceFailure(error: unknown): PersistenceFailure {
    const status = (error as { response?: { status?: number } } | null)?.response?.status;
    if (status === 409) return 'conflict';
    if (status === 503) return 'unavailable';
    return 'failed';
}
