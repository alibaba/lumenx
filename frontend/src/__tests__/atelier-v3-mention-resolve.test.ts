// Mention auto-attach resolver — extracted as a pure unit test rather
// than testing through the giant shell integration. The four-priority
// match (exact → ci-exact → prefix → contains) is the part most likely
// to regress when someone touches the dispatcher in handleComposerSubmit.
import { describe, it, expect } from "vitest";

// Mirror the resolver from AtelierShellV3.tsx. Keeping this as a copy
// (rather than exporting from the shell) avoids dragging the entire
// AtelierShellV3 module into a unit test. If the shell's resolver
// changes, this test needs the same change — call out the duplicate
// in any PR that touches one side.

interface Node {
  type: string;
  title?: string;
  data?: { intent?: string; body?: string };
  media_urls?: string[];
}

function labelsOf(n: Node): string[] {
  const title = n.title || "";
  const intent = n.data?.intent || "";
  const body = (n.data?.body || "").slice(0, 40);
  return [title, intent, body].filter((s) => s.length > 0);
}

function resolveMention(query: string, pool: Node[]): Node | null {
  const q = query.trim();
  const ql = q.toLowerCase();
  const tries: Array<(labels: string[]) => boolean> = [
    (labels) => labels.some((l) => l === q),
    (labels) => labels.some((l) => l.toLowerCase() === ql),
    (labels) => labels.some((l) => l.toLowerCase().startsWith(ql)),
    (labels) => labels.some((l) => l.toLowerCase().includes(ql)),
  ];
  for (const test of tries) {
    const matches = pool.filter((n) => test(labelsOf(n)));
    if (matches.length === 1) return matches[0];
  }
  return null;
}

const candidatePool = (extra: Node[] = []): Node[] => [
  { type: "image", title: "Hero shot", media_urls: ["a.png"] },
  { type: "image", title: "Background", media_urls: ["b.png"] },
  { type: "image", title: "Closeup", data: { intent: "Portrait" }, media_urls: ["c.png"] },
  ...extra,
];

describe("mention resolver — priority cascade", () => {
  it("exact match wins immediately", () => {
    const result = resolveMention("Hero shot", candidatePool());
    expect(result?.title).toBe("Hero shot");
  });

  it("falls back to case-insensitive exact when no exact hit", () => {
    const result = resolveMention("hero shot", candidatePool());
    expect(result?.title).toBe("Hero shot");
  });

  it("falls back to prefix when no ci-exact hit", () => {
    const result = resolveMention("hero", candidatePool());
    expect(result?.title).toBe("Hero shot");
  });

  it("falls back to contains when no prefix hit", () => {
    const result = resolveMention("ground", candidatePool());
    expect(result?.title).toBe("Background");
  });

  it("ambiguous prefix bails to contains and bails again if still multiple", () => {
    const pool = candidatePool([
      { type: "image", title: "Hero closeup", media_urls: ["d.png"] },
    ]);
    // 'hero' prefix-matches both 'Hero shot' and 'Hero closeup'.
    // Contains also matches both. Resolver returns null.
    const result = resolveMention("hero", pool);
    expect(result).toBeNull();
  });

  it("matches against data.intent when title doesn't hit", () => {
    const result = resolveMention("Portrait", candidatePool());
    expect(result?.title).toBe("Closeup");
  });

  it("returns null for unmatched query", () => {
    const result = resolveMention("xyzzy", candidatePool());
    expect(result).toBeNull();
  });
});
