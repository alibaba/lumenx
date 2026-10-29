import { expect, it } from 'vitest';
import { API_URL } from '@/lib/apiUrl';
import { getAssetUrl, getAssetUrlWithTimestamp } from '@/lib/utils';
import motion from '@/__tests__/fixtures/studio-motion-terminal-followup-wire.json';
import images from '@/__tests__/fixtures/inline-raster-display.json';

// Keep the actual motion projection/receipt fixture immutable. JPEG/WebP bytes
// are copied from existing backend image fixtures; provenance is in the JSON.
const event = motion.cases.find(item => item.case === 'completed_history_then_explicit_followup')
    ?.events.find(item => item.label === 'first_unprojected_project');
const projection = event?.body as { characters?: Array<{ full_body?: { image_variants?: Array<{ url?: string }> } }> } | undefined;
const png = projection?.characters?.[0].full_body?.image_variants?.[0].url;
if (typeof png !== 'string' || !png.startsWith('data:image/png;base64,')) throw new Error('Missing captured inline PNG source');
const rasters = [['PNG', png], ['JPEG', images.jpeg], ['WebP', images.webp]] as const;

it.each(rasters)('keeps the existing %s raster URL byte-for-byte, without a files route', (_kind, uri) => {
    expect(getAssetUrl(uri)).toBe(uri);
    expect(getAssetUrl(getAssetUrl(uri))).toBe(uri);
});

it.each(rasters)('does not append cache metadata to %s raster bytes', (_kind, uri) => {
    expect(getAssetUrlWithTimestamp(uri, 123)).toBe(uri);
    expect(getAssetUrlWithTimestamp(uri)).toBe(uri);
});

it.each([
    ['SVG', 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='],
    ['raw SVG', 'data:image/svg+xml,<svg onload="alert(1)"></svg>'],
    ['HTML', 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='],
    ['raw HTML', 'data:text/html,<script>alert(1)</script>'],
    ['arbitrary binary', 'data:application/octet-stream;base64,YQ=='],
    ['audio', 'data:audio/wav;base64,YQ=='],
    ['video', 'data:video/mp4;base64,YQ=='],
    ['GIF outside the inline contract', 'data:image/gif;base64,R0lGODlh'],
    ['BMP outside the inline contract', 'data:image/bmp;base64,Qk0='],
    ['missing MIME', 'data:;base64,YQ=='],
    ['empty payload', 'data:image/png;base64,'],
    ['missing base64 marker', png.replace(';base64,', ',')],
    ['additional MIME parameter', png.replace(';base64,', ';charset=utf-8;base64,')],
    ['nonstandard MIME alias', png.replace('image/png', 'image/x-png')],
    ['leading whitespace', ` ${png}`],
    ['trailing newline', `${png}\n`],
    ['trailing carriage return', `${png}\r`],
    ['trailing Unicode line separator', `${png}\u2028`],
    ['HTML after raster bytes', `${png}<img src=x onerror="alert(1)">`],
    ['attribute breakout after raster bytes', `${png}" onerror="alert(1)`],
    ['HTML entity after raster bytes', `${png}&quot;`],
    ['percent-encoded markup after raster bytes', `${png}%3Cscript%3E`],
    ['encoded payload whitespace', png.replace(';base64,', ';base64, ')],
    ['invalid base64 alphabet', 'data:image/png;base64,iVBORw0KGgo!'],
    ['incomplete base64 quantum', 'data:image/png;base64,iVBORw0KGgo'],
    ['padding in the middle', 'data:image/png;base64,iVB=Rw0KGgo='],
    ['too much padding', 'data:image/png;base64,iVBORw0KGgo==='],
    ['query appended to bytes', `${png}?t=123`],
    ['fragment appended to bytes', `${png}#preview`],
    ['uppercase scheme outside the canonical contract', png.replace('data:', 'DATA:')],
    ['uppercase MIME outside the canonical contract', png.replace('image/png', 'IMAGE/PNG')],
    ['PNG MIME over HTML bytes', 'data:image/png;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='],
    ['PNG bytes mislabeled as JPEG', png.replace('image/png', 'image/jpeg')],
    ['JPEG bytes mislabeled as WebP', images.jpeg.replace('image/jpeg', 'image/webp')],
    ['WebP bytes mislabeled as PNG', images.webp.replace('image/webp', 'image/png')],
] as const)('rejects %s instead of exposing an active data URL or inventing a managed file', (_kind, uri) => {
    expect(getAssetUrl(uri)).toBe('');
    expect(getAssetUrlWithTimestamp(uri, 123)).toBe('');
});

it.each([
    ['assets/角色 A.png', `${API_URL}/files/assets/%E8%A7%92%E8%89%B2%20A.png`],
    ['/uploads/source.png', `${API_URL}/files/uploads/source.png`],
    ['https://media.example.test/image.png?signature=kept&x=1', 'https://media.example.test/image.png?signature=kept&x=1'],
    ['http://media.example.test/image.png', 'http://media.example.test/image.png'],
    ['blob:https://media.example.test/source', 'blob:https://media.example.test/source'],
] as const)('preserves the existing non-inline address behavior for %s', (raw, expected) => {
    expect(getAssetUrl(raw)).toBe(expected);
});

it('retains cache metadata for normal managed and remote media addresses', () => {
    expect(getAssetUrlWithTimestamp('assets/image.png', 123)).toBe(`${API_URL}/files/assets/image.png?t=123`);
    expect(getAssetUrlWithTimestamp('https://media.example.test/image.png?signature=kept', 123))
        .toBe('https://media.example.test/image.png?signature=kept&t=123');
});

it.each([undefined, null, ''])('keeps an absent URL empty (%s)', value => {
    expect(getAssetUrl(value)).toBe('');
    expect(getAssetUrlWithTimestamp(value, 123)).toBe('');
});
