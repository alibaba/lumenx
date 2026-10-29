// Shared "Luminous Atelier" cover primitives.
//
// Pure, framework-free helpers for rendering deterministic typographic covers
// (used when an asset / project has no derived image). Imported by Studio cards
// and the Atelier asset library so a given seed always yields the same cover.

// Fine film-grain texture (matches the Atelier mockup), layered over derived
// covers via mix-blend overlay so a flat gradient gains a tactile, photographic feel.
export const GRAIN_URL =
    "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

// Deterministic typographic-cover palette for items without an image. Each entry
// blends a cinematic accent (teal / amber) into warm graphite so a serif title
// stays legible; the entry is chosen by hashing a seed (e.g. id, falling back to
// title) so a given item keeps the same cover across renders.
const COVER_GRADIENTS = [
    "linear-gradient(150deg, var(--color-primary) -10%, var(--color-bg-inset) 72%)",
    "linear-gradient(135deg, var(--color-accent) -15%, var(--color-bg-surface) 70%)",
    "linear-gradient(160deg, var(--color-primary) -18%, var(--color-bg-elevated) 55%, var(--color-bg-inset) 100%)",
    "linear-gradient(140deg, var(--color-bg-surface) 0%, var(--color-primary) 165%)",
];

export function coverGradient(seed: string): string {
    let h = 0;
    for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
    return COVER_GRADIENTS[h % COVER_GRADIENTS.length];
}
