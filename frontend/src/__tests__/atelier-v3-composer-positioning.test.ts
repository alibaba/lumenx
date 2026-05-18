import { describe, it, expect } from "vitest";
import { composerPlacement } from "@/components/atelier/v3/Composer/positioning";

const VP = { width: 1440, height: 900, rightRailWidth: 396 };

describe("composerPlacement", () => {
  it("centers when anchor is null", () => {
    const { left, top } = composerPlacement(null, VP);
    expect(left).toBe(Math.round((1440 - 520) / 2));
    expect(top).toBeGreaterThan(0);
  });

  it("places below anchor, left-aligned with anchor", () => {
    // Default gap is 14px now (was 16). Test the relationship, not the
    // exact magic number.
    const p = composerPlacement({ x: 64, y: 264, width: 240, height: 110 }, VP);
    expect(p.left).toBe(64);
    expect(p.top).toBeGreaterThanOrEqual(264 + 110);
    expect(p.top).toBeLessThanOrEqual(264 + 110 + 24);
  });

  it("nudges inward when composer would overflow viewport right edge", () => {
    // anchor.x at 1300 + composer width 520 = 1820 > viewport 1440. The
    // composer slides left so its right edge stays in-viewport. Old
    // behavior also avoided the right rail; the new contract intentionally
    // allows rail overlap (composer is z-40, rail z-30) so the composer
    // stays as close to the anchor as possible.
    const p = composerPlacement({ x: 1300, y: 100, width: 240, height: 110 }, VP);
    expect(p.left + 520).toBeLessThanOrEqual(1440);
    expect(p.left).toBeGreaterThan(800); // not pushed all the way left
  });

  it("never goes left of the inner viewport gutter", () => {
    const p = composerPlacement({ x: -200, y: 100, width: 240, height: 110 }, VP);
    expect(p.left).toBeGreaterThanOrEqual(12);
  });

  it("respects custom composer width and gap", () => {
    const p = composerPlacement({ x: 100, y: 100, width: 200, height: 100 }, VP, { width: 400, gap: 24 });
    expect(p.left).toBe(100);
    expect(p.top).toBe(100 + 100 + 24);
  });

  it("flips above when there is no room below and there is room above", () => {
    // Viewport 900 tall. Anchor at y=750 height=100 → bottom at 850. Below
    // would need height 320 → 850+14+320 = 1184 > 888. Above: 750-14-320 = 416 ≥ 12.
    const p = composerPlacement({ x: 100, y: 750, width: 240, height: 100 }, VP);
    expect(p.top).toBeLessThan(750);
  });
});
