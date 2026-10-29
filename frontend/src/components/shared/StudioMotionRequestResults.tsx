'use client';

import { useTranslations } from 'next-intl';
import PreviewVideo from './preview/PreviewVideo';
import { studioMotionCompletionProof, studioMotionRecoveryAction } from '@/lib/studioMotionRequest';
import type { useStudioMotionRequests } from '@/lib/useStudioMotionRequests';

export default function StudioMotionRequestResults({ requests }: { requests: ReturnType<typeof useStudioMotionRequests> }) {
    const t = useTranslations('motionRequest');
    if (!requests.results.length && !requests.readFailed && !requests.submitFailed && !requests.actionFailed) return null;
    const busy = requests.reading || requests.submitting || requests.acting;
    return <section aria-label={t('results')} className="rounded-lg border border-glass-border bg-glass p-3 text-xs space-y-2">
        <div className="flex justify-between gap-2">
            <span>{t('results')}</span>
            <button type="button" onClick={() => { void requests.refresh(); }} disabled={busy}
                className="text-primary disabled:opacity-40">{t('read')}</button>
        </div>
        {requests.results.map(result => {
            const task = result.receipt?.task, phase = task?.provider_execution?.phase;
            const action = requests.releasedResult(result) ? null : studioMotionRecoveryAction(result);
            const proof = studioMotionCompletionProof(result), path = task?.provider_execution?.artifact?.relative_path;
            const label = result.status === 'not_accepted' ? 'notAccepted'
                : result.status !== 'confirmed' || !task ? 'unconfirmed'
                    : task.status === 'failed' || phase === 'cancelled' ? 'failed'
                        : task.status === 'needs_confirmation' || phase === 'completed' && !proof ? 'unconfirmed'
                            : phase === 'accepted' ? 'accepted' : task.status === 'completed' ? 'completed' : 'running';
            return <div key={result.journal.request.client_request_id} className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                    {proof && typeof path === 'string' && <PreviewVideo src={path} alt={t('completed')} className="h-16 w-16 rounded" clickToLightbox />}
                    <p role="status">{t(label)}</p>
                    {action && <button type="button" onClick={() => { void requests.recover(result); }} disabled={busy}
                        className="text-primary disabled:opacity-40">{t(action)}</button>}
                </div>
                {requests.canKeepResult(result) && <div className="space-y-1">
                    <p>{t('keepHint')}</p>
                    <button type="button" onClick={() => { void requests.keepResult(result); }} disabled={busy}
                        className="text-primary disabled:opacity-40">{t('keep')}</button>
                </div>}
                {requests.keptResult(result) && <p role="status">{t('kept')}</p>}
            </div>;
        })}
        {requests.readFailed && <p role="status">{t('readFailed')}</p>}
        {requests.submitFailed && <p role="status">{t('notSubmitted')}</p>}
        {requests.actionFailed && <p role="status">{t('actionFailed')}</p>}
    </section>;
}
