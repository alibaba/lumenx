import { describe, it, expect } from "vitest";
import { resolveAtelierVariant } from "@/components/atelier/v3/useAtelierVariant";

// v0.4.x canvas-uplift branch flipped the default: v3 IS the new default
// shell, and legacy is only reachable via an explicit `?atelier=legacy`
// opt-out. See useAtelierVariant.ts for the rationale (the old `?atelier=v3`
// trap silently fell back to legacy and made the new design invisible).
describe("resolveAtelierVariant", () => {
  it("returns 'v3' when search param atelier=v3", () => {
    expect(resolveAtelierVariant("?atelier=v3")).toBe("v3");
  });
  it("returns 'v3' by default (v3 is the canvas-uplift default)", () => {
    expect(resolveAtelierVariant("?")).toBe("v3");
    expect(resolveAtelierVariant("?foo=bar")).toBe("v3");
    expect(resolveAtelierVariant("")).toBe("v3");
  });
  it("returns 'legacy' only on explicit ?atelier=legacy opt-out", () => {
    expect(resolveAtelierVariant("?atelier=legacy")).toBe("legacy");
  });
  it("does not throw on malformed input", () => {
    expect(() => resolveAtelierVariant("???x=%%")).not.toThrow();
    expect(resolveAtelierVariant("???x=%%")).toBe("v3");
  });
});
