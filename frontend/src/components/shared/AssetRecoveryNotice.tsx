"use client";
import { useTranslations } from 'next-intl';

/** Displays only the committed asset fact; callers own all readback and session checks. */
export default function AssetRecoveryNotice({ onRead, reading = false, readFailed = false }: {
  onRead?: () => void; reading?: boolean; readFailed?: boolean;
}) {
  const t = useTranslations('assetRecovery');
  return <div role="status" className="rounded-lg border border-glass-border bg-surface-inset p-3 text-sm text-text-secondary">
    <p className="font-medium text-foreground">{t('title')}</p>
    <p className="mt-1">{t('body')}</p>
    <p className="mt-1 text-xs">{t('newRequest')}</p>
    {readFailed && <p role="alert" className="mt-1">{t('readFailed')}</p>}
    {onRead && <button type="button" disabled={reading} onClick={onRead} className="mt-2 underline disabled:opacity-50">{t('readAgain')}</button>}
  </div>;
}
