// Region geometry — pure helpers shared between AtelierShellV3 (drag
// hit-tests, group-into-region command) and unit tests.
//
// A region is just an AtelierNode with `type === "region"`. Its geometry
// uses the same x / y / width / height fields any node has. A node is a
// child of a region iff `node.data.region_id === region.id`.
//
// These helpers stay pure (plain object math) so the shell's drag loop
// can call them on every pointermove without forcing a re-render or a
// store read.

export const REGION_DEFAULT_WIDTH = 600;
export const REGION_DEFAULT_HEIGHT = 400;
/** Padding added around the union bbox when wrapping a selection into a
 *  region via Cmd+G. 32px gives the title bar + corner handles room
 *  without making the frame visually swamp its contents. */
export const REGION_GROUP_PADDING = 32;

export interface RegionLike {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface NodeLike {
  id: string;
  type: string;
  x: number;
  y: number;
  width?: number;
  height?: number;
  data?: Record<string, unknown>;
}

/** True if the point lies inside the region's bounding box (inclusive
 *  on all four edges). Used by hover hit-tests during drag. */
export function isPointInRegion(
  point: { x: number; y: number },
  region: RegionLike,
): boolean {
  return (
    point.x >= region.x &&
    point.x <= region.x + region.width &&
    point.y >= region.y &&
    point.y <= region.y + region.height
  );
}

/** True when the node's center point is inside the region. We use center
 *  (not corner / not full bbox) for spatial attach during drag because
 *  it matches how users think — "I'm dropping the node here" — and it's
 *  forgiving when a node is slightly larger than expected. */
export function isNodeCenterInRegion(node: NodeLike, region: RegionLike): boolean {
  const w = node.width ?? 0;
  const h = node.height ?? 0;
  const cx = node.x + w / 2;
  const cy = node.y + h / 2;
  return isPointInRegion({ x: cx, y: cy }, region);
}

/** True if the entire node bounding box is inside the region. Useful when
 *  the caller wants strict containment (e.g. "all selected nodes must
 *  fit inside this region before allowing a save-as-workflow"). */
export function isNodeFullyInRegion(node: NodeLike, region: RegionLike): boolean {
  const w = node.width ?? 0;
  const h = node.height ?? 0;
  return (
    node.x >= region.x &&
    node.y >= region.y &&
    node.x + w <= region.x + region.width &&
    node.y + h <= region.y + region.height
  );
}

/** Find the first region in the input list that contains the point. The
 *  caller is responsible for ordering — pass top-most z-index first if
 *  overlapping regions are possible. v1 forbids nesting so overlap is a
 *  user-mistake case rather than a designed-for state, but the helper
 *  doesn't enforce that — it just walks the list in order. */
export function findRegionAtPoint(
  point: { x: number; y: number },
  regions: RegionLike[],
): RegionLike | null {
  for (const r of regions) {
    if (isPointInRegion(point, r)) return r;
  }
  return null;
}

/** Read the `region_id` off a node's data payload, returning null when
 *  unset or the wrong type. Defensive against legacy nodes that wrote
 *  numeric or null values. */
export function readRegionId(node: NodeLike): string | null {
  const raw = (node.data ?? {})["region_id"];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

/** Return only the nodes whose `data.region_id` equals the given id. */
export function nodesInRegion<T extends NodeLike>(nodes: T[], regionId: string): T[] {
  return nodes.filter((n) => readRegionId(n) === regionId);
}

/** Return only the region-typed nodes from a list. */
export function regionsFromNodes<T extends NodeLike>(nodes: T[]): T[] {
  return nodes.filter((n) => n.type === "region");
}

/** Compute a bounding box that wraps every input node, with optional
 *  padding. Returns null on empty input. Used by the Cmd+G handler in
 *  the shell to derive the new region's bounds from the current
 *  selection. */
export function computeRegionBoundsForNodes(
  nodes: NodeLike[],
  padding: number = REGION_GROUP_PADDING,
): { x: number; y: number; width: number; height: number } | null {
  if (nodes.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    const w = n.width ?? 0;
    const h = n.height ?? 0;
    if (n.x < minX) minX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.x + w > maxX) maxX = n.x + w;
    if (n.y + h > maxY) maxY = n.y + h;
  }
  return {
    x: minX - padding,
    y: minY - padding,
    width: maxX - minX + padding * 2,
    height: maxY - minY + padding * 2,
  };
}
