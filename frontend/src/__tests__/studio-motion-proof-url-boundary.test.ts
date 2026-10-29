// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioMotionRequest, validateStudioMotionReceipt } from '@/lib/studioMotionRequest';
import type { StudioMotionRequestBody } from '@/lib/api';
import { getAssetUrl } from '@/lib/utils';
import duration5 from '@/__tests__/fixtures/studio-motion-duration5-wire.json';

type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: StudioMotionRequestBody };
function actual(label: string): Event {
    const found = (duration5 as unknown as { cases: { case: string; events: Event[] }[] }).cases
        .find(item => item.case === 'lifecycle_script_duration5')?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual HTTP evidence: ${label}`);
    return structuredClone(found);
}
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected fixture object');
    return value as Obj;
}
function journal() {
    const { client_request_id, ...input } = actual('accepted_post').request_body!;
    return beginStudioMotionRequest({
        environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: 'script', id: 'project' }, input,
        selectedImageId: 'selected-image', idFactory: () => client_request_id, now: () => 7,
    }).journal;
}
beforeEach(() => { localStorage.clear(); clearRuntimeEnvironment(); });
afterEach(() => { clearRuntimeEnvironment(); });

it.each([
    ['task', 'historical?query'], ['attempt', 'historical#fragment'],
])('rejects a jointly changed %s identifier whose proof becomes URL metadata (%s)', (field, unsafe) => {
    const stored = journal(), receipt = actual('completed_get').body, task = obj(receipt.task);
    const request = obj(receipt.request), record = obj(task.provider_execution);
    if (field === 'task') { receipt.task_id = unsafe; request.task_id = unsafe; task.id = unsafe; }
    else { request.attempt_id = unsafe; record.attempt_id = unsafe; }
    const path = `video/provider-${String(task.id)}-${String(record.attempt_id)}.mp4`;
    for (const key of ['artifact', 'prepared_artifact']) {
        const proof = obj(record[key]); proof.attempt_id = record.attempt_id;
        proof.relative_path = path;
    }
    // The actual browser URL helper retains ?/#; this proof cannot name the
    // same managed file when transported as a URL path.
    const url = new URL(getAssetUrl(path), 'http://localhost');
    expect(decodeURI(url.pathname)).not.toBe(`/files/${path}`);
    expect(Boolean(url.search || url.hash)).toBe(true);
    expect(() => validateStudioMotionReceipt(receipt, stored)).toThrow('studio_motion_receipt_invalid');
});
