// Region geometry — pure helpers used by AtelierShellV3 to do hit-tests
// during drag (spatial attach/detach), bounding-box wrapping for
// Cmd+G group-into-region, and child-set lookups.
//
// These are pure functions on plain objects so they're cheap to unit
// test in isolation; the shell wires them to live AtelierNode data.
import { describe, it, expect } from "vitest";
import {
  isPointInRegion,
  isNodeCenterInRegion,
  isNodeFullyInRegion,
  findRegionAtPoint,
  nodesInRegion,
  readRegionId,
  regionsFromNodes,
  computeRegionBoundsForNodes,
  REGION_DEFAULT_WIDTH,
  REGION_DEFAULT_HEIGHT,
  REGION_GROUP_PADDING,
  type RegionLike,
  type NodeLike,
} from "@/components/atelier/v3/regionGeometry";

const region = (
  id: string,
  x: number,
  y: number,
  w = 600,
  h = 400,
): RegionLike => ({ id, x, y, width: w, height: h });

const node = (
  id: string,
  x: number,
  y: number,
  w = 200,
  h = 120,
  region_id?: string,
  type = "video",
): NodeLike => ({
  id,
  type,
  x,
  y,
  width: w,
  height: h,
  data: region_id ? { region_id } : {},
});

describe("isPointInRegion", () => {
  const r = region("r1", 100, 100, 400, 300);
  it("returns true for a point inside", () => {
    expect(isPointInRegion({ x: 200, y: 200 }, r)).toBe(true);
  });
  it("returns true on the edge (inclusive)", () => {
    expect(isPointInRegion({ x: 100, y: 100 }, r)).toBe(true);
    expect(isPointInRegion({ x: 500, y: 400 }, r)).toBe(true);
  });
  it("returns false for a point outside", () => {
    expect(isPointInRegion({ x: 50, y: 200 }, r)).toBe(false);
    expect(isPointInRegion({ x: 600, y: 200 }, r)).toBe(false);
    expect(isPointInRegion({ x: 200, y: 50 }, r)).toBe(false);
    expect(isPointInRegion({ x: 200, y: 500 }, r)).toBe(false);
  });
});

describe("isNodeCenterInRegion", () => {
  const r = region("r1", 100, 100, 400, 300);
  it("uses the node center, not corner", () => {
    // Node at (50,50) size 100×100 — corner is outside, center (100,100)
    // is on the edge → inside.
    const n = node("n1", 50, 50, 100, 100);
    expect(isNodeCenterInRegion(n, r)).toBe(true);
  });
  it("returns false when center is outside even if part of node overlaps", () => {
    // Node at (450,250) size 200×120 — center (550,310) is right of region.
    const n = node("n1", 450, 250, 200, 120);
    expect(isNodeCenterInRegion(n, r)).toBe(false);
  });
  it("falls back to (0,0) size when width/height missing", () => {
    const bare: NodeLike = { id: "n1", type: "idea", x: 200, y: 200 };
    expect(isNodeCenterInRegion(bare, r)).toBe(true);
  });
});

describe("isNodeFullyInRegion", () => {
  const r = region("r1", 100, 100, 400, 300);
  it("returns true when whole bounding box fits", () => {
    expect(isNodeFullyInRegion(node("n1", 200, 200, 100, 80), r)).toBe(true);
  });
  it("returns false when any edge sticks out", () => {
    expect(isNodeFullyInRegion(node("n1", 50, 200, 100, 80), r)).toBe(false);
    expect(isNodeFullyInRegion(node("n1", 200, 200, 400, 80), r)).toBe(false);
  });
});

describe("findRegionAtPoint", () => {
  it("returns null for empty input", () => {
    expect(findRegionAtPoint({ x: 0, y: 0 }, [])).toBeNull();
  });
  it("returns the region containing the point", () => {
    const r1 = region("r1", 0, 0, 200, 200);
    const r2 = region("r2", 300, 0, 200, 200);
    expect(findRegionAtPoint({ x: 50, y: 50 }, [r1, r2])?.id).toBe("r1");
    expect(findRegionAtPoint({ x: 350, y: 50 }, [r1, r2])?.id).toBe("r2");
  });
  it("returns the first match when regions overlap (caller controls z-order)", () => {
    const r1 = region("r1", 0, 0, 400, 400);
    const r2 = region("r2", 200, 200, 400, 400);
    // Point lies in both. Caller passes top-most first.
    expect(findRegionAtPoint({ x: 250, y: 250 }, [r2, r1])?.id).toBe("r2");
    expect(findRegionAtPoint({ x: 250, y: 250 }, [r1, r2])?.id).toBe("r1");
  });
  it("returns null for a point outside all regions", () => {
    const r = region("r1", 0, 0, 100, 100);
    expect(findRegionAtPoint({ x: 200, y: 200 }, [r])).toBeNull();
  });
});

describe("nodesInRegion + readRegionId", () => {
  it("returns nodes whose data.region_id matches", () => {
    const nodes = [
      node("a", 0, 0, 100, 100, "r1"),
      node("b", 0, 0, 100, 100, "r2"),
      node("c", 0, 0, 100, 100, "r1"),
      node("d", 0, 0, 100, 100), // unattached
    ];
    expect(nodesInRegion(nodes, "r1").map((n) => n.id)).toEqual(["a", "c"]);
    expect(nodesInRegion(nodes, "r2").map((n) => n.id)).toEqual(["b"]);
    expect(nodesInRegion(nodes, "r3")).toEqual([]);
  });
  it("readRegionId returns null when not set", () => {
    expect(readRegionId(node("x", 0, 0))).toBeNull();
  });
  it("readRegionId returns the value when set", () => {
    expect(readRegionId(node("x", 0, 0, 100, 100, "r1"))).toBe("r1");
  });
  it("readRegionId tolerates non-string values (defensive)", () => {
    const n: NodeLike = { id: "x", type: "video", x: 0, y: 0, data: { region_id: 42 as unknown as string } };
    expect(readRegionId(n)).toBeNull();
  });
});

describe("regionsFromNodes", () => {
  it("filters to type === 'region'", () => {
    const nodes = [
      node("a", 0, 0, 100, 100, undefined, "video"),
      node("b", 0, 0, 100, 100, undefined, "region"),
      node("c", 0, 0, 100, 100, undefined, "image"),
      node("d", 0, 0, 100, 100, undefined, "region"),
    ];
    expect(regionsFromNodes(nodes).map((n) => n.id)).toEqual(["b", "d"]);
  });
});

describe("computeRegionBoundsForNodes", () => {
  it("returns null for empty input", () => {
    expect(computeRegionBoundsForNodes([])).toBeNull();
  });
  it("wraps a single node with default padding", () => {
    const result = computeRegionBoundsForNodes([node("a", 100, 100, 200, 120)]);
    expect(result).toEqual({
      x: 100 - REGION_GROUP_PADDING,
      y: 100 - REGION_GROUP_PADDING,
      width: 200 + REGION_GROUP_PADDING * 2,
      height: 120 + REGION_GROUP_PADDING * 2,
    });
  });
  it("wraps multiple nodes by their union bounding box", () => {
    const result = computeRegionBoundsForNodes([
      node("a", 100, 100, 200, 100),
      node("b", 500, 300, 200, 100),
    ]);
    expect(result).toEqual({
      x: 100 - REGION_GROUP_PADDING,
      y: 100 - REGION_GROUP_PADDING,
      width: 600 + REGION_GROUP_PADDING * 2, // 700 - 100 = 600
      height: 300 + REGION_GROUP_PADDING * 2, // 400 - 100 = 300
    });
  });
  it("respects custom padding", () => {
    const result = computeRegionBoundsForNodes(
      [node("a", 0, 0, 100, 100)],
      8,
    );
    expect(result).toEqual({ x: -8, y: -8, width: 116, height: 116 });
  });
  it("treats missing width/height as zero size", () => {
    const bare: NodeLike = { id: "a", type: "idea", x: 100, y: 200 };
    const result = computeRegionBoundsForNodes([bare], 0);
    expect(result).toEqual({ x: 100, y: 200, width: 0, height: 0 });
  });
});

describe("constants", () => {
  it("exposes default region size", () => {
    expect(REGION_DEFAULT_WIDTH).toBeGreaterThan(0);
    expect(REGION_DEFAULT_HEIGHT).toBeGreaterThan(0);
  });
  it("exposes group padding", () => {
    expect(REGION_GROUP_PADDING).toBeGreaterThan(0);
  });
});
