import type { AtelierNode } from "@/lib/atelierTypes";
import { getAssetUrl } from "@/lib/utils";

export type MediaKind = "image" | "video" | "audio";

export interface MediaNodeView {
  id: string;
  kind: MediaKind;
  src?: string;
  poster?: string;
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

/** Project an AtelierNode into a MediaNodeView. Returns null if the node is not a media type. */
export function toMediaNodeView(
  node: AtelierNode,
  opts?: { selectedNodeId?: string | null }
): MediaNodeView | null {
  if (node.type !== "image" && node.type !== "video" && node.type !== "audio") return null;
  const data = node.data ?? {};
  return {
    id: node.id,
    kind: node.type as MediaKind,
    // Canvas media must resolve through getAssetUrl like every other
    // surface (AssetLibrary already does) — raw relative media_urls
    // otherwise 404 against the frontend origin and the card renders
    // black while the library thumbnail works (uplift batch-1 fix).
    src: node.media_urls?.[0] ? getAssetUrl(node.media_urls[0]) : undefined,
    poster: typeof data.poster_url === "string" ? getAssetUrl(data.poster_url) : undefined,
    filename: typeof data.filename === "string" ? data.filename : undefined,
    duration: typeof data.duration === "number" && Number.isFinite(data.duration) && data.duration >= 0
      ? `${Math.floor(data.duration / 60)}:${String(Math.floor(data.duration % 60)).padStart(2, "0")}`
      : typeof data.duration === "string" ? data.duration : undefined,
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
