# Atelier Frontend v0.3 Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor `AtelierShell.tsx` (1647 lines monolith) into a v0.3 IA: media-as-node, slim DraftNode, single floating Composer, SelectionActionBar, narrow Toolbar + BottomNavRail + Agent-only Right Rail. Each PR is independently mergeable behind a `?atelier=v3` feature flag; final PR flips default and removes v0.2 code.

**Architecture:** Component split — every node type and chrome region becomes its own file under `frontend/src/components/atelier/v3/`. `AtelierStore` actions remain (no backend change). Candidates are still `data.candidates[]` on the parent draft node in the backend; v0.3 simply *renders* them as separate canvas tiles with derived positions. Old `AtelierShell.tsx` stays intact until the cutover PR; new code lives behind a URL flag so each PR ships safely.

**Tech Stack:** Next.js 14 · React 18 · TypeScript (strict) · Tailwind · Zustand · Vitest + React Testing Library · lucide-react.

**Source-of-truth references:**
- `docs/design/atelier-DESIGN.md` (v0.3) — design spec
- `docs/design/prototypes/atelier-phase-bcd.html` — hi-fi prototype (open with `open docs/design/prototypes/atelier-phase-bcd.html`)
- `docs/agents/deliverables/atelier-frontend-redesign-prd.md` — PRD §11 / §15

---

## Conventions

- **TDD per task**: write failing test → run (verify FAIL) → minimal impl → run (verify PASS) → commit. Tests live next to the component or in `frontend/src/__tests__/atelier-v3-*.test.tsx`.
- **Imports use `@/...` alias** (already configured in `tsconfig.json`).
- **Tailwind classes only** — no custom CSS unless extending `globals.css` is necessary.
- **All v0.3 components in `components/atelier/v3/`** — clean blast radius from v0.2 `AtelierShell.tsx` until cutover.
- **No backend changes** — `AtelierNode.type` is `string` (loose), so we can mint `"draft" | "plan" | "audio"` types without touching Pydantic.
- **Feature flag**: a small `useAtelierVariant()` hook reads `URLSearchParams.get("atelier")`. `?atelier=v3` → render `<AtelierShellV3 />`; otherwise → existing `<AtelierShell />`.
- **Commit cadence**: one commit per task minimum (test + impl together if very small; usually 1 commit for failing test, 1 for impl + passing).
- **Run before commit**: `cd frontend && npm run typecheck && npm run test -- <changed-files>`.

---

## PR Slicing

| PR | Tasks | Touches | Ship gate |
|----|-------|---------|-----------|
| **PR 1** Scaffolding + MediaNode | 1, 2 | `v3/` folder, `MediaNode.tsx`, hook | Tests green; renders nothing in prod yet |
| **PR 2** Slim node types | 3, 4, 5 | DraftNode, PlanNode, IdeaNode | Tests green |
| **PR 3** Action Bar | 6 | SelectionActionBar | Tests green |
| **PR 4** Bottom Nav Rail | 7 | BottomNavRail, Minimap | Tests green |
| **PR 5** Narrow Toolbar + Agent-only Rail | 8, 9 | ToolbarV3, RightRailV3 | Tests green |
| **PR 6** Composer | 10 | Composer + chip dropdowns + tabs | Tests green |
| **PR 7** Integration behind flag | 11 | `AtelierShellV3.tsx` + flag wiring | `?atelier=v3` works end-to-end manually |
| **PR 8** Cutover | 12 | Delete v0.2 code, flip default | Smoke + visual QA pass |

After PR 8: a subsequent maintenance PR can rename `v3/` → flat `components/atelier/`.

---

## Task 1 — Scaffold v3 folder + feature flag hook

**Files:**
- Create: `frontend/src/components/atelier/v3/index.ts` (barrel)
- Create: `frontend/src/components/atelier/v3/useAtelierVariant.ts`
- Test: `frontend/src/__tests__/atelier-v3-variant.test.ts`

**Step 1 — Failing test:**

```ts
// frontend/src/__tests__/atelier-v3-variant.test.ts
import { describe, it, expect } from "vitest";
import { resolveAtelierVariant } from "@/components/atelier/v3/useAtelierVariant";

describe("resolveAtelierVariant", () => {
  it("returns 'v3' when search param atelier=v3", () => {
    expect(resolveAtelierVariant("?atelier=v3")).toBe("v3");
  });
  it("returns 'legacy' otherwise", () => {
    expect(resolveAtelierVariant("?")).toBe("legacy");
    expect(resolveAtelierVariant("?foo=bar")).toBe("legacy");
    expect(resolveAtelierVariant("")).toBe("legacy");
  });
});
```

**Step 2 — Run, verify FAIL:**

```bash
cd frontend && npm run test -- src/__tests__/atelier-v3-variant.test.ts
# Expected: FAIL — module not found
```

**Step 3 — Minimal implementation:**

```ts
// frontend/src/components/atelier/v3/useAtelierVariant.ts
"use client";
import { useEffect, useState } from "react";

export type AtelierVariant = "v3" | "legacy";

export function resolveAtelierVariant(search: string): AtelierVariant {
  try {
    const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
    return params.get("atelier") === "v3" ? "v3" : "legacy";
  } catch {
    return "legacy";
  }
}

export function useAtelierVariant(): AtelierVariant {
  const [variant, setVariant] = useState<AtelierVariant>("legacy");
  useEffect(() => {
    if (typeof window === "undefined") return;
    setVariant(resolveAtelierVariant(window.location.search));
    const onChange = () => setVariant(resolveAtelierVariant(window.location.search));
    window.addEventListener("popstate", onChange);
    return () => window.removeEventListener("popstate", onChange);
  }, []);
  return variant;
}
```

```ts
// frontend/src/components/atelier/v3/index.ts
export { useAtelierVariant, resolveAtelierVariant, type AtelierVariant } from "./useAtelierVariant";
```

**Step 4 — Run, verify PASS:**

```bash
cd frontend && npm run test -- src/__tests__/atelier-v3-variant.test.ts
npm run typecheck
# Expected: both green
```

**Step 5 — Commit:**

```bash
git add frontend/src/components/atelier/v3/ frontend/src/__tests__/atelier-v3-variant.test.ts
git commit -m "feat(atelier): scaffold v3 folder and ?atelier=v3 variant flag"
```

---

## Task 2 — `MediaNode` (image / video / audio media-as-node)

**Spec ref:** DESIGN.md §6.3 / §6.3-bis / §6.3-tris.

**Files:**
- Create: `frontend/src/components/atelier/v3/MediaNode.tsx`
- Create: `frontend/src/components/atelier/v3/types.ts` (shared v3 types)
- Test: `frontend/src/__tests__/atelier-v3-medianode.test.tsx`

**Step 1 — Shared types:**

```ts
// frontend/src/components/atelier/v3/types.ts
import type { AtelierNode } from "@/lib/api";

export type MediaKind = "image" | "video" | "audio";

export interface MediaNodeView {
  id: string;
  kind: MediaKind;
  src?: string;
  filename?: string;
  duration?: string;
  status: "draft" | "pending" | "processing" | "completed" | "failed";
  progress?: number;
  selected?: boolean;
  selectedAsTake?: boolean;
  x: number;
  y: number;
  width?: number;
  height?: number;
}

/** Project an AtelierNode into a MediaNodeView. */
export function toMediaNodeView(node: AtelierNode, opts?: { selectedNodeId?: string | null }): MediaNodeView | null {
  if (node.type !== "image" && node.type !== "video" && node.type !== "audio") return null;
  const data = node.data ?? {};
  return {
    id: node.id,
    kind: node.type as MediaKind,
    src: node.media_urls?.[0] ?? undefined,
    filename: typeof data.filename === "string" ? data.filename : undefined,
    duration: typeof data.duration === "string" ? data.duration : undefined,
    status: (node.status as MediaNodeView["status"]) ?? "draft",
    progress: typeof data.progress === "number" ? data.progress : undefined,
    selected: opts?.selectedNodeId === node.id,
    selectedAsTake: data.selected_as_take === true,
    x: node.x,
    y: node.y,
    width: typeof node.width === "number" && node.width > 0 ? node.width : undefined,
    height: typeof node.height === "number" && node.height > 0 ? node.height : undefined,
  };
}
```

**Step 2 — Failing test:**

```tsx
// frontend/src/__tests__/atelier-v3-medianode.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MediaNode } from "@/components/atelier/v3/MediaNode";

describe("MediaNode", () => {
  it("renders an image with filename chip on hover", () => {
    render(
      <MediaNode kind="image" src="https://example.com/a.png" filename="A.JPG"
                 status="completed" x={0} y={0} id="n1" />
    );
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://example.com/a.png");
    // filename only renders in DOM; CSS hides it until hover. We assert it's in the tree.
    expect(screen.getByText("A.JPG")).toBeInTheDocument();
  });

  it("renders processing overlay for status=processing with progress", () => {
    render(<MediaNode kind="video" status="processing" progress={47} x={0} y={0} id="n2" />);
    expect(screen.getByText("47%")).toBeInTheDocument();
  });

  it("renders persistent Selected chip when selectedAsTake", () => {
    render(<MediaNode kind="video" src="https://example.com/v.mp4" status="completed"
                     selectedAsTake x={0} y={0} id="n3" />);
    expect(screen.getByText(/Selected/i)).toBeInTheDocument();
  });

  it("clamps width <= 240 even if larger requested", () => {
    const { container } = render(
      <MediaNode kind="image" src="https://example.com/a.png" status="completed"
                 width={400} height={400} x={0} y={0} id="n4" />
    );
    const root = container.firstElementChild as HTMLElement;
    expect(parseInt(root.style.width)).toBeLessThanOrEqual(240);
  });
});
```

**Step 3 — Run, verify FAIL.**

**Step 4 — Implementation** (port from prototype `atelier-phase-bcd.html` line ~418, adapt to React component file):

```tsx
// frontend/src/components/atelier/v3/MediaNode.tsx
"use client";
import { Check, ImageIcon, Loader2, Play, AlertTriangle, Video, Volume2 } from "lucide-react";
import type { MediaKind } from "./types";

interface Props {
  id: string;
  kind: MediaKind;
  src?: string;
  filename?: string;
  duration?: string;
  status: "draft" | "pending" | "processing" | "completed" | "failed";
  progress?: number;
  selected?: boolean;
  selectedAsTake?: boolean;
  x: number;
  y: number;
  width?: number;
  height?: number;
  onSelect?: (id: string) => void;
}

const DEFAULTS: Record<MediaKind, { w: number; h: number }> = {
  image: { w: 180, h: 180 },
  video: { w: 200, h: 113 },
  audio: { w: 200, h: 56 },
};

export function MediaNode(props: Props) {
  const { id, kind, src, filename, duration, status, progress,
          selected, selectedAsTake, x, y, onSelect } = props;
  const def = DEFAULTS[kind];
  const w = Math.min(props.width ?? def.w, 240);
  const h = props.height ?? def.h;

  const ringClass =
    selected || selectedAsTake ? "ring-2 ring-primary"
    : status === "processing" || status === "pending" ? "ring-1 ring-blue-400/60"
    : status === "failed" ? "ring-1 ring-red-400/60"
    : "";

  return (
    <div
      role="button"
      tabIndex={0}
      onPointerDown={() => onSelect?.(id)}
      className={`group absolute overflow-hidden rounded-md bg-black/30 ${ringClass}`}
      style={{ width: w, height: h, transform: `translate(${x}px, ${y}px)` }}
    >
      {/* Body */}
      {kind === "audio" ? (
        <div className="flex h-full w-full items-center gap-2 px-3">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-white/10 text-foreground/90">
            <Play size={11} />
          </span>
          <div className="flex flex-1 items-center gap-[2px]">
            {Array.from({ length: 28 }).map((_, i) => (
              <span key={i} className="block w-[2px] rounded-sm bg-white/35"
                    style={{ height: `${10 + Math.abs(Math.sin(i * 0.7)) * 22}px` }} />
            ))}
          </div>
        </div>
      ) : (
        src ? <img src={src} alt={filename ?? ""} className="h-full w-full object-cover" /> : null
      )}

      {kind === "video" && status === "completed" && (
        <span className="absolute inset-0 m-auto grid h-8 w-8 place-items-center rounded-full bg-black/55 text-white/95 opacity-60 backdrop-blur-sm transition group-hover:opacity-100">
          <Play size={14} />
        </span>
      )}

      {(status === "processing" || status === "pending") && (
        <div className="absolute inset-0 grid place-items-center bg-blue-400/[0.18] backdrop-blur-[1px]">
          <div className="flex flex-col items-center gap-1">
            <Loader2 size={20} className="animate-spin text-blue-100" />
            {typeof progress === "number" && (
              <span className="font-mono text-[10px] text-blue-100">{progress}%</span>
            )}
          </div>
        </div>
      )}
      {status === "failed" && (
        <div className="absolute inset-0 grid place-items-center bg-red-400/[0.18] text-center">
          <AlertTriangle size={18} className="text-red-200" />
        </div>
      )}

      {/* Hover-only chrome */}
      <span className="absolute left-1 top-1 hidden rounded bg-black/65 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-white/85 group-hover:block">
        {kind === "video" ? <Video size={10} className="inline -mt-0.5" /> :
         kind === "audio" ? <Volume2 size={10} className="inline -mt-0.5" /> :
                            <ImageIcon size={10} className="inline -mt-0.5" />} {kind}
      </span>
      {filename && (
        <span className="absolute right-1 bottom-1 hidden max-w-[70%] truncate rounded bg-black/65 px-1.5 py-0.5 font-mono text-[10px] text-white/80 group-hover:block">
          {filename}
        </span>
      )}
      {duration && (
        <span className="absolute left-1 bottom-1 hidden rounded bg-black/65 px-1.5 py-0.5 font-mono text-[10px] text-white/80 group-hover:block">
          {duration}
        </span>
      )}

      {selectedAsTake && (
        <span className="absolute left-1 bottom-1 inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-white shadow-[0_0_0_2px_rgba(0,0,0,0.35)]">
          <Check size={10} /> Selected
        </span>
      )}
    </div>
  );
}
```

**Step 5 — Update barrel:** add `export * from "./MediaNode";` and `export * from "./types";` to `v3/index.ts`.

**Step 6 — Run, verify PASS:**

```bash
cd frontend && npm run test -- src/__tests__/atelier-v3-medianode.test.tsx && npm run typecheck
```

**Step 7 — Commit:**

```bash
git add frontend/src/components/atelier/v3/ frontend/src/__tests__/atelier-v3-medianode.test.tsx
git commit -m "feat(atelier): add v3 MediaNode (image/video/audio media-as-node)"
```

---

## Task 3 — `DraftNode`

**Spec ref:** DESIGN.md §6.4. Slim ~240×~110 intent card.

**Files:**
- Create: `frontend/src/components/atelier/v3/DraftNode.tsx`
- Test: `frontend/src/__tests__/atelier-v3-draftnode.test.tsx`

**Test cases (write failing first):**

```tsx
import { render, screen } from "@testing-library/react";
import { DraftNode } from "@/components/atelier/v3/DraftNode";

it("renders intent label, model chip and config summary", () => {
  render(<DraftNode id="d1" status="draft" intent="Cinematic" modelLabel="Wan 2.7"
                    configSummary="1280×720 · 5s · 4×" x={0} y={0} />);
  expect(screen.getByText("Cinematic")).toBeInTheDocument();
  expect(screen.getByText("Wan 2.7")).toBeInTheDocument();
  expect(screen.getByText(/1280×720/)).toBeInTheDocument();
});

it("shows amber border when status=draft (awaiting approval)", () => {
  const { container } = render(<DraftNode id="d2" status="draft" intent="X"
                    modelLabel="M" configSummary="—" x={0} y={0} />);
  expect(container.firstElementChild?.className).toMatch(/amber/);
});

it("renders ref thumbnails when refs provided", () => {
  render(<DraftNode id="d3" status="draft" intent="X" modelLabel="M" configSummary="—"
                    x={0} y={0} refs={["a.jpg", "b.jpg"]} />);
  expect(screen.getAllByRole("img")).toHaveLength(2);
});
```

**Implementation skeleton:** port from prototype `atelier-phase-bcd.html` line ~536 (`const DraftNode`).

**Commit:** `feat(atelier): add v3 DraftNode (slim intent card)`

---

## Task 4 — `IdeaNode` (rewrite, slim)

**Spec ref:** DESIGN.md §6.2. ~220×~80 amber text bubble.

**Files:**
- Create: `frontend/src/components/atelier/v3/IdeaNode.tsx`
- Test: `frontend/src/__tests__/atelier-v3-ideanode.test.tsx`

**Test cases:**

```tsx
it("renders body text", () => {
  render(<IdeaNode id="i1" body="The protagonist stops at the edge." x={0} y={0} />);
  expect(screen.getByText(/protagonist stops/)).toBeInTheDocument();
});

it("uses amber tinted edge", () => {
  const { container } = render(<IdeaNode id="i2" body="x" x={0} y={0} />);
  expect(container.firstElementChild?.className).toMatch(/amber/);
});
```

Implementation: prototype `atelier-phase-bcd.html` line ~607.

**Commit:** `feat(atelier): add v3 IdeaNode (slim amber text bubble)`

---

## Task 5 — `PlanNode`

**Spec ref:** DESIGN.md §6.5. Agent-created plan card with bullet list.

**Files:**
- Create: `frontend/src/components/atelier/v3/PlanNode.tsx`
- Test: `frontend/src/__tests__/atelier-v3-plannode.test.tsx`

**Test cases:**

```tsx
it("renders title and bullet list", () => {
  render(<PlanNode id="p1" title="3-direction" bullets={["a", "b", "c"]} x={0} y={0} />);
  expect(screen.getByText("3-direction")).toBeInTheDocument();
  expect(screen.getByText("a")).toBeInTheDocument();
});

it("shows 'PLAN · by Agent' tag", () => {
  render(<PlanNode id="p2" title="x" bullets={[]} x={0} y={0} />);
  expect(screen.getByText(/PLAN · by Agent/i)).toBeInTheDocument();
});
```

Implementation: prototype line ~585.

**Commit:** `feat(atelier): add v3 PlanNode (Agent plan + bullets)`

---

## Task 6 — `SelectionActionBar`

**Spec ref:** DESIGN.md §5.2-bis. Floating icon row above selection.

**Files:**
- Create: `frontend/src/components/atelier/v3/SelectionActionBar.tsx`
- Test: `frontend/src/__tests__/atelier-v3-actionbar.test.tsx`

**Test cases:**

```tsx
it("renders Play + Branch + Delete for kind=video", () => {
  const onAct = vi.fn();
  render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
  expect(screen.getByLabelText(/play/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/delete/i)).toBeInTheDocument();
});

it("hides Add to Sequence for kind=image", () => {
  render(<SelectionActionBar kind="image" x={0} y={100} width={200} onAct={() => {}} />);
  expect(screen.queryByLabelText(/sequence/i)).toBeNull();
});

it("calls onAct with action key when icon clicked", () => {
  const onAct = vi.fn();
  render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
  fireEvent.click(screen.getByLabelText(/^delete$/i));
  expect(onAct).toHaveBeenCalledWith("delete");
});
```

Implementation: prototype line ~495. Use `aria-label` on each icon button so tests can target.

**Commit:** `feat(atelier): add v3 SelectionActionBar (figma-style floating toolbar)`

---

## Task 7 — `BottomNavRail` + `Minimap`

**Spec ref:** DESIGN.md §5.8. Bottom-left zoom + minimap toggle.

**Files:**
- Create: `frontend/src/components/atelier/v3/BottomNavRail.tsx`
- Create: `frontend/src/components/atelier/v3/Minimap.tsx`
- Test: `frontend/src/__tests__/atelier-v3-bottomnav.test.tsx`

**Test cases:**

```tsx
it("renders zoom value", () => {
  render(<BottomNavRail zoom={85} onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
  expect(screen.getByText("85%")).toBeInTheDocument();
});

it("calls onFit when fit button clicked", () => {
  const onFit = vi.fn();
  render(<BottomNavRail zoom={100} onZoomChange={() => {}} onFit={onFit} onToggleMinimap={() => {}} />);
  fireEvent.click(screen.getByLabelText(/fit view/i));
  expect(onFit).toHaveBeenCalled();
});

it("Minimap renders one dot per node", () => {
  render(<Minimap nodes={[{x:0,y:0},{x:50,y:50}]} viewport={{x:0,y:0,w:100,h:100}} />);
  const dots = document.querySelectorAll('[data-testid="minimap-dot"]');
  expect(dots).toHaveLength(2);
});
```

Implementation: prototype lines `BottomNavRail` and `MinimapWidget`.

**Commit:** `feat(atelier): add v3 BottomNavRail and Minimap`

---

## Task 8 — `ToolbarV3` (narrow, create + agent only)

**Spec ref:** DESIGN.md §5.1. Drop zoom/fit/redo from toolbar, keep create + history + Ask Agent.

**Files:**
- Create: `frontend/src/components/atelier/v3/ToolbarV3.tsx`
- Test: `frontend/src/__tests__/atelier-v3-toolbar.test.tsx`

**Test cases:**

```tsx
it("emits create-video when Video button clicked", () => {
  const onCreate = vi.fn();
  render(<ToolbarV3 onCreate={onCreate} onAskAgent={() => {}} onUndo={() => {}} onRedo={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /new video/i }));
  expect(onCreate).toHaveBeenCalledWith("video");
});

it("does NOT render zoom controls (moved to BottomNavRail)", () => {
  render(<ToolbarV3 onCreate={() => {}} onAskAgent={() => {}} onUndo={() => {}} onRedo={() => {}} />);
  expect(screen.queryByLabelText(/zoom/i)).toBeNull();
  expect(screen.queryByLabelText(/fit/i)).toBeNull();
});
```

**Commit:** `feat(atelier): add v3 narrow Toolbar (create + Ask Agent only)`

---

## Task 9 — `RightRailV3` (Agent-only, drop Node tab)

**Spec ref:** DESIGN.md §5.3.

**Files:**
- Create: `frontend/src/components/atelier/v3/RightRailV3.tsx`
- Reuse: `frontend/src/components/atelier/AgentPanelTrace.tsx` for inner conversation surface
- Test: `frontend/src/__tests__/atelier-v3-rightrail.test.tsx`

**Test cases:**

```tsx
it("renders Agent header with status", () => {
  render(<RightRailV3 mode="on_request" agentStatus="active"><div>body</div></RightRailV3>);
  expect(screen.getByText(/Creative Agent/)).toBeInTheDocument();
});

it("does NOT render Node tab", () => {
  render(<RightRailV3 mode="on_request" agentStatus="active"><div /></RightRailV3>);
  expect(screen.queryByRole("tab", { name: /node/i })).toBeNull();
});
```

**Commit:** `feat(atelier): add v3 RightRail (Agent-only, no Node tab)`

---

## Task 10 — `Composer` (the v0.3 core editor)

**Spec ref:** DESIGN.md §5.2. **The largest task; allocate the most time.**

**Files:**
- Create: `frontend/src/components/atelier/v3/Composer/index.tsx`
- Create: `frontend/src/components/atelier/v3/Composer/ChipDropdown.tsx`
- Create: `frontend/src/components/atelier/v3/Composer/CapabilityIcon.tsx`
- Test: `frontend/src/__tests__/atelier-v3-composer.test.tsx`
- Test: `frontend/src/__tests__/atelier-v3-composer-positioning.test.ts`

**Sub-step A — positioning math first (pure function, easy to test):**

```ts
// frontend/src/components/atelier/v3/Composer/positioning.ts
export interface ComposerAnchor { x: number; y: number; width: number; height: number; }
export function composerPlacement(
  anchor: ComposerAnchor | null,
  viewport: { width: number; height: number; rightRailWidth: number },
  composer: { width: number; gap?: number } = { width: 520, gap: 16 }
): { left: number; top: number } {
  const gap = composer.gap ?? 16;
  if (!anchor) {
    return {
      left: Math.max(16, (viewport.width - composer.width) / 2),
      top:  Math.max(16, viewport.height / 3),
    };
  }
  const desiredLeft = anchor.x;
  const maxLeft     = viewport.width - viewport.rightRailWidth - composer.width - gap;
  return {
    left: Math.max(16, Math.min(desiredLeft, maxLeft)),
    top:  anchor.y + anchor.height + gap,
  };
}
```

Test (write FIRST, run, verify FAIL, then add function):

```ts
// frontend/src/__tests__/atelier-v3-composer-positioning.test.ts
import { describe, it, expect } from "vitest";
import { composerPlacement } from "@/components/atelier/v3/Composer/positioning";

const VP = { width: 1440, height: 900, rightRailWidth: 396 };

it("centers when no anchor", () => {
  const { left, top } = composerPlacement(null, VP);
  expect(left).toBe((1440 - 520) / 2);
  expect(top).toBeGreaterThan(0);
});

it("places below anchor, left-aligned", () => {
  const p = composerPlacement({ x: 64, y: 264, width: 240, height: 110 }, VP);
  expect(p.left).toBe(64);
  expect(p.top).toBe(264 + 110 + 16);
});

it("clamps left so composer never overlaps right rail", () => {
  const p = composerPlacement({ x: 1200, y: 100, width: 240, height: 110 }, VP);
  expect(p.left).toBeLessThanOrEqual(1440 - 396 - 520 - 16);
});

it("never goes left of 16", () => {
  const p = composerPlacement({ x: -200, y: 100, width: 240, height: 110 }, VP);
  expect(p.left).toBeGreaterThanOrEqual(16);
});
```

**Sub-step B — `ChipDropdown` component** (button that pops a popover; v1 popover can be a simple `<details>` for now):

```tsx
// frontend/src/components/atelier/v3/Composer/ChipDropdown.tsx
"use client";
import { ChevronDown } from "lucide-react";
import { ReactNode } from "react";

interface Props {
  label: string;
  value: string;
  options?: { value: string; label: string }[];
  onChange?: (v: string) => void;
  primary?: boolean;
  disabled?: boolean;
  children?: ReactNode;
}

export function ChipDropdown({ label, value, options, onChange, primary, disabled, children }: Props) {
  return (
    <details className="relative inline-block">
      <summary className={`btn-tip inline-flex cursor-pointer list-none items-center gap-1 rounded-md border border-glass-border px-2 py-1.5 text-[11px] transition ${
        primary ? "bg-glass text-foreground hover:bg-hover-bg" :
        disabled ? "bg-glass text-text-muted/60 cursor-not-allowed" :
                   "bg-glass text-text-secondary hover:bg-hover-bg hover:text-foreground"
      }`} aria-label={label} aria-disabled={disabled}>
        <span className="font-medium">{value}</span>
        <ChevronDown size={10} className="text-text-muted" />
      </summary>
      <div className="absolute left-0 top-full z-50 mt-1 min-w-[160px] rounded-md border border-glass-border bg-elevated p-1 shadow-2xl shadow-black/40">
        {children ?? options?.map(o => (
          <button key={o.value}
            onClick={() => onChange?.(o.value)}
            className={`block w-full rounded px-2 py-1 text-left text-[12px] hover:bg-hover-bg ${
              o.value === value ? "text-primary" : "text-text-secondary"
            }`}>
            {o.label}
          </button>
        ))}
      </div>
    </details>
  );
}
```

**Sub-step C — main `Composer` shell** (port from prototype line ~642 + `CapabilityIcon` line ~362). Uses `composerPlacement()` for positioning.

Test cases for the Composer shell:

```tsx
it("renders all 7 generation tabs", () => {
  render(<Composer activeTab="I2V" prompt="" onSubmit={() => {}} />);
  ["T2I","I2I","T2V","I2V","R2V","V2V","Audio"].forEach(t =>
    expect(screen.getByRole("button", { name: t })).toBeInTheDocument()
  );
});

it("disables submit when capability mismatch", () => {
  render(<Composer activeTab="I2V" prompt="x" onSubmit={() => {}} showCapabilityMismatch />);
  expect(screen.getByLabelText(/submit/i)).toBeDisabled();
});

it("calls onSubmit with current chip values", () => {
  const onSubmit = vi.fn();
  render(<Composer activeTab="I2V" prompt="hi" onSubmit={onSubmit}
                   modelLabel="Wan 2.7" aspect="16:9 · 720p" duration="5s" count="4×" />);
  fireEvent.click(screen.getByLabelText(/submit/i));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ modelLabel: "Wan 2.7" }));
});
```

**Commit cadence:** at least 3 commits in this task.

```bash
git commit -m "feat(atelier): add Composer positioning math"
git commit -m "feat(atelier): add ChipDropdown primitive"
git commit -m "feat(atelier): add v3 Composer shell with submit + mismatch handling"
```

---

## Task 11 — Integration: `AtelierShellV3` behind `?atelier=v3`

**Goal:** Wire all v3 components into a working canvas. Old `AtelierShell.tsx` stays untouched. `frontend/src/app/page.tsx` switches on `useAtelierVariant()`.

**Files:**
- Create: `frontend/src/components/atelier/v3/AtelierShellV3.tsx`
- Modify: `frontend/src/app/page.tsx` — small conditional render
- Test: `frontend/src/__tests__/atelier-v3-shell.test.tsx`

**Modify `page.tsx`** (locate around line 26, the dynamic import; add v3 import below):

```tsx
const AtelierShell   = dynamic(() => import("@/components/atelier/AtelierShell"),                { ssr: false });
const AtelierShellV3 = dynamic(() => import("@/components/atelier/v3/AtelierShellV3").then(m => m.AtelierShellV3), { ssr: false });
```

Inside the `currentView === 'atelier'` branch (line ~547), select by variant:

```tsx
if (currentView === 'atelier') {
  return atelierVariant === 'v3' ? <AtelierShellV3 /> : <AtelierShell />;
}
```

Add `const atelierVariant = useAtelierVariant();` near the other hooks.

**`AtelierShellV3` responsibilities** (~600 lines, replaces 1647 of the legacy):

1. Pull store state with `useAtelierStore()` (no new actions needed for v1).
2. Layout 5 zones: Toolbar (top-left) · Canvas (full) · BottomNavRail (bottom-left) · RightRailV3 (right) · SequenceStrip (bottom inset).
3. Render nodes: walk `project.nodes` and dispatch by `node.type`:
   - `image | video | audio` → `<MediaNode>`
   - `idea` (or absent type containing only `data.body`) → `<IdeaNode>`
   - `plan` → `<PlanNode>`
   - `video` with `status === "draft"` and `data.intent` → `<DraftNode>`
   - **Candidates**: for each `node` with `type==="video"` and non-empty `data.candidates`, derive 4 virtual `MediaNodeView`s positioned to the right of the parent and render. (No backend change.)
4. SVG edge layer:
   - draw `derived_from` between parent draft and each candidate (use existing `lib/atelierCanvas.ts::buildReferenceLinks`)
   - draw `uses_reference` between image refs and video drafts
5. Selection state: `selectedNodeId` from store. When set, render `<SelectionActionBar>` above the node and `<Composer>` below it (only if the selected node is a Draft or Video Media).
6. Wire actions:
   - Toolbar `+ Video` → `store.createVideoNode()` then `setSelectedNodeId(newId)`
   - Composer Submit → `store.createVideoCandidates(selectedNodeId, config)`
   - SelectionActionBar `Select as take` → `store.selectCandidate(parentId, candidateId)`
   - SelectionActionBar `Delete` → confirm + `store.deleteCandidate` or `store.deleteAtelierNode`

**Tests** (smoke + regression):

```tsx
import { render, screen } from "@testing-library/react";
import { AtelierShellV3 } from "@/components/atelier/v3/AtelierShellV3";

it("renders five-zone layout", () => {
  render(<AtelierShellV3 />);
  expect(screen.getByRole("toolbar", { name: /atelier/i })).toBeInTheDocument();
  expect(screen.getByRole("region",  { name: /right rail/i })).toBeInTheDocument();
});

it("does NOT show Generate / Regenerate inside any node card on canvas", () => {
  render(<AtelierShellV3 />);
  expect(screen.queryByRole("button", { name: /generate candidates/i })).toBeNull();
});
```

**Manual smoke** (record in PR description):

```bash
cd frontend && npm run dev
# in browser:
open "http://localhost:3008/?atelier=v3#/atelier"
```

Verify:
- Default route still renders v0.2 (no `?atelier=v3`)
- `?atelier=v3` renders new shell
- Create a video node → DraftNode appears
- Click DraftNode → Composer opens below, ActionBar above
- Submit → candidates appear as separate MediaNodes connected by edges
- Select a take → emerald ring + ✓ chip persists

**Commit:**

```bash
git commit -m "feat(atelier): wire AtelierShellV3 behind ?atelier=v3 flag"
```

---

## Task 12 — Cutover: flip default + delete v0.2

**Pre-conditions:** PR 7 merged for ≥ 3 days; no regressions reported via `?atelier=v3`; design QA signed off (use checklist from DESIGN.md §14).

**Files:**
- Modify: `frontend/src/app/page.tsx` — default to v3, `?atelier=legacy` opts back into v0.2 for one release
- Delete: `frontend/src/components/atelier/AtelierShell.tsx`
- Delete: tests that targeted v0.2 internals (after porting any still-relevant assertions to v3)
- Modify: `frontend/src/__tests__/atelier-store.test.ts` — drop assertions tied to v0.2 fat card
- Modify: `frontend/src/__tests__/atelier-canvas.test.ts` — keep edge math tests; drop card-layout tests
- Promote: `frontend/src/components/atelier/v3/*` → `frontend/src/components/atelier/*` (separate commit so diff is reviewable)

**Step 1 — Flip default:** in `page.tsx`, change selector to default v3, allow `?atelier=legacy`:

```tsx
return atelierVariant === 'legacy' ? <AtelierShell /> : <AtelierShellV3 />;
```

**Step 2 — Run full suite:**

```bash
cd frontend && npm run typecheck && npm run test:all
```

**Step 3 — Manual visual QA against DESIGN.md §14 checklist** (fill into PR description with screenshots).

**Step 4 — Commit (flip):**

```bash
git commit -m "refactor(atelier): default to v3 shell, ?atelier=legacy keeps old fallback"
```

**Step 5 — In a SECOND commit, delete legacy code:**

```bash
git rm frontend/src/components/atelier/AtelierShell.tsx
git commit -m "refactor(atelier): remove v0.2 AtelierShell after v3 cutover"
```

**Step 6 — In a THIRD commit, promote v3/ to top-level:**

```bash
git mv frontend/src/components/atelier/v3/* frontend/src/components/atelier/
# fix imports with sed, run tests, commit
git commit -m "refactor(atelier): promote v3/ components to atelier/ root"
```

---

## Risk register

| Risk | Mitigation |
|------|-----------|
| Composer popover positioning conflicts with sequence strip / right rail | Pure-function `composerPlacement` is unit-tested for all bounds; manual test with viewport <1280 |
| Candidates rendered as virtual nodes drift from real `data.candidates[]` after re-render | Treat parent draft node's `data.candidates` as single source of truth; derive virtual MediaNodeViews freshly each render |
| Drag handlers conflict between MediaNode and SelectionActionBar | ActionBar uses `pointer-events: auto` with stop-propagation on click; node drag handler ignores when target is an ActionBar button |
| Viewport sizes < 1024 break layout | DESIGN.md §13.2 already specifies fallback (collapse rails to handles); this plan does not implement collapse — track as follow-up |
| Visual drift from prototype after Tailwind class adjustments | DESIGN.md §3 tokens are the source of truth; if prototype diverges from production code, fix the prototype to match production |
| Backend `AtelierNode.type === "draft"` not yet recognized by Pydantic | Currently typed as loose `str`; verify with `pytest tests/test_atelier_core.py -k 'create_node' -v` after first DraftNode creation |

---

## Post-cutover (out of scope here, log as follow-ups)

- Real chip dropdowns with full `modelCatalog.ts` integration (Composer model selector currently hard-codes labels)
- Drag-to-connect for new edges
- Multi-select bounding box + bulk SelectionActionBar
- Collapse rails on `<1024px` viewport
- Replay agent runs in canvas (PRD §8 audit log)

---

## Verification

- `cd frontend && npm run typecheck` — must pass each commit
- `cd frontend && npm run test:all` — must pass each commit
- Manual: `npm run dev` → `http://localhost:3008/?atelier=v3#/atelier` → walk through DESIGN.md §14 checklist
- Backend smoke: `pytest -q tests/test_atelier_core.py` (no behavioral change expected, but confirms no regression)

---

## Critical files reference

- `frontend/src/components/atelier/AtelierShell.tsx:73-76` — `statusTone()` color groups (preserve exactly in v3)
- `frontend/src/components/atelier/AtelierShell.tsx:113-118` — permission mode label dictionary (reuse verbatim)
- `frontend/src/components/atelier/AgentPanelTrace.tsx` — Session/Readiness/History; reuse inside `RightRailV3`
- `frontend/src/lib/atelierCanvas.ts::buildReferenceLinks` — edge math (reuse for MediaNode → DraftNode edges)
- `frontend/src/lib/atelierAgentPlanning.ts` — view-model helpers (no change)
- `frontend/src/lib/api.ts:187-207` — `AtelierNode` interface (no change)
- `docs/design/atelier-DESIGN.md` — design contract (consult §3 for tokens, §6 for nodes, §10 for state matrix)
- `docs/design/prototypes/atelier-phase-bcd.html` — visual reference (open in browser)
