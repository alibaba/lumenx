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

  it("respects custom composer width and gap", () => {
    const p = composerPlacement({ x: 100, y: 100, width: 200, height: 100 }, VP, { width: 400, gap: 24 });
    expect(p.left).toBe(100);
    expect(p.top).toBe(100 + 100 + 24);
  });
});
