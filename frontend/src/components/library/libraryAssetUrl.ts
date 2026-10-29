import { getAssetUrl } from '@/lib/utils';

/** Resolve the library's persisted legacy prefixes consistently for viewing and download. */
export function getLibraryAssetUrl(path: string | null | undefined): string {
  return getAssetUrl(path?.replace(/^output\//, '').replace(/^\/?files\//, '').replace(/^\/+/, ''));
}
