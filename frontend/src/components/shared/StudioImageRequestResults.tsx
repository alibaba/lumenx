'use client';

import { useTranslations } from 'next-intl';
import type { useStudioAssetImageRequests } from '@/lib/useStudioAssetImageRequests';
import PreviewImage from './preview/PreviewImage';

/** Read-only presentation; the existing hook retains all request and journal ownership. */
export default function StudioImageRequestResults({ requests }: { requests: ReturnType<typeof useStudioAssetImageRequests> }) {
    const t = useTranslations('castWorkbench');
    const recovery = useTranslations('assetRecovery');
    if (!requests.results.length && !requests.readFailed && !requests.submitFailed) return null;
    return <section aria-label={t('requestResults')} className="rounded-lg border border-glass-border bg-glass p-3 text-xs space-y-2">
        <div className="flex justify-between items-center gap-2">
            <span>{t('requestResults')}</span>
            <button onClick={() => { void requests.refresh(); }} disabled={requests.reading} className="text-primary disabled:opacity-40">{t('requestRead')}</button>
        </div>
        {requests.results.map(result => <div key={result.journal.request.client_request_id} className="space-y-1">
            {['root', 'owner', 'target', 'inputs', 'projection'].some(part => result.receipt?.reason === `studio_image_request_${part}_changed`) && <p>{t('requestAssociationChanged')}</p>}
            {(result.receipt?.request.members ?? Array.from({ length: result.journal.request.batch_size ?? 1 }, (_, index) => ({ attempt_id: `${result.journal.request.client_request_id}-${index}`, status: 'needs_confirmation' as const, output_ref: undefined }))).map((member, index) => <div key={member.attempt_id} className="flex items-center gap-2">
                {member.status === 'completed' && member.output_ref && <PreviewImage src={member.output_ref} alt={t('requestMember', { index: index + 1 })} className="h-12 w-12 rounded" clickToLightbox />}
                <span>{t('requestMember', { index: index + 1 })}</span><span>{t(member.status === 'cancelled' && result.receipt?.request.channel === 'image' ? 'requestNotGenerated' : `requestStatus.${member.status}`)}</span>
            </div>)}
            {requests.canKeepResult?.(result) && <div className="space-y-1">
                <p>{t('storyboardKeepResultHint')}</p>
                <button type="button" onClick={() => { void requests.keepResult(result); }} disabled={requests.reading || requests.submitting}
                    className="text-primary disabled:opacity-40">{t('storyboardKeepResult')}</button>
            </div>}
            {requests.keptResult?.(result) && <p role="status">{t('storyboardResultKept')}</p>}
        </div>)}
        {requests.unknown && <p>{recovery('newRequest')}</p>}
        {requests.readFailed && <p role="status">{recovery('readFailed')}</p>}
        {requests.submitFailed && <p role="status">{t('requestNotSubmitted')}</p>}
        {requests.readbackFailed && <p role="status">{t('requestReadbackFailed')}</p>}
    </section>;
}
