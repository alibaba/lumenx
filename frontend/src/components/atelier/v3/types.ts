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
    src: node.media_urls?.[0],
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
