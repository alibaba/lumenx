// @vitest-environment happy-dom
//
// AssetLibrary — left-edge drawer that lists project media. Tests cover
// the user-facing contract: filtering by kind, image-only secondary
// filter (Character / Scene / Prop), search, category-cycling pill,
// and the collapsed-state handle.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AssetLibrary } from "@/components/atelier/v3/AssetLibrary";
import type { AtelierNode } from "@/lib/api";

function img(id: string, opts: Partial<AtelierNode> = {}): AtelierNode {
  return {
    id,
    project_id: "p1",
    type: "image",
    title: opts.title ?? id,
    prompt: "",
    status: "completed",
    x: 0,
    y: 0,
    width: 244,
    height: 224,
    media_urls: ["https://example.com/" + id + ".png"],
    data: opts.data ?? {},
    created_by: "user",
    created_at: 0,
    updated_at: 0,
  } as AtelierNode;
}

function vid(id: string): AtelierNode {
  return {
    ...img(id),
    type: "video",
    status: "completed",
    media_urls: ["https://example.com/" + id + ".mp4"],
  } as AtelierNode;
}

function noop() {/* no-op for tests that don't care about handlers */}

describe("AssetLibrary — collapsed state", () => {
  it("renders only the edge handle when closed", () => {
    render(<AssetLibrary nodes={[]} open={false} onToggle={noop} />);
    expect(screen.getByLabelText("Open asset library")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /asset library/i })).toBeNull();
  });

  it("clicking the handle calls onToggle", () => {
    const onToggle = vi.fn();
    render(<AssetLibrary nodes={[]} open={false} onToggle={onToggle} />);
    fireEvent.click(screen.getByLabelText("Open asset library"));
    expect(onToggle).toHaveBeenCalled();
  });
});

describe("AssetLibrary — open state", () => {
  it("lists every image / video / audio node", () => {
    const nodes = [img("hero"), vid("clip1"), img("bg")];
    render(<AssetLibrary nodes={nodes} open onToggle={noop} />);
    expect(screen.getByText("hero")).toBeInTheDocument();
    expect(screen.getByText("clip1")).toBeInTheDocument();
    expect(screen.getByText("bg")).toBeInTheDocument();
  });

  it("excludes draft video nodes (no media)", () => {
    const draft: AtelierNode = {
      ...vid("draft1"),
      status: "draft",
      media_urls: [],
    } as AtelierNode;
    render(<AssetLibrary nodes={[draft]} open onToggle={noop} />);
    expect(screen.queryByText("draft1")).toBeNull();
  });

  it("kind filter narrows the grid to images", () => {
    render(<AssetLibrary nodes={[img("hero"), vid("clip1")]} open onToggle={noop} />);
    fireEvent.click(screen.getByRole("tab", { name: /image/i }));
    expect(screen.getByText("hero")).toBeInTheDocument();
    expect(screen.queryByText("clip1")).toBeNull();
  });

  it("search filters by substring on title", () => {
    render(<AssetLibrary nodes={[img("hero"), img("background")]} open onToggle={noop} />);
    const input = screen.getByPlaceholderText(/search by title/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "back" } });
    expect(screen.getByText("background")).toBeInTheDocument();
    expect(screen.queryByText("hero")).toBeNull();
  });

  it("empty state when no assets exist at all", () => {
    render(<AssetLibrary nodes={[]} open onToggle={noop} />);
    expect(screen.getByText(/no assets yet/i)).toBeInTheDocument();
  });

  it("empty state when search yields nothing but assets exist", () => {
    render(<AssetLibrary nodes={[img("hero")]} open onToggle={noop} />);
    const input = screen.getByPlaceholderText(/search by title/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "xyzzy" } });
    expect(screen.getByText(/no matches/i)).toBeInTheDocument();
  });
});

describe("AssetLibrary — image category secondary filter", () => {
  const charImg = img("warrior", { data: { category: "character" } });
  const sceneImg = img("alley", { data: { category: "scene" } });
  const propImg = img("sword", { data: { category: "prop" } });
  const untagged = img("misc");

  it("does NOT show category chips when kind is not 'image'", () => {
    render(<AssetLibrary nodes={[charImg, vid("clip1")]} open onToggle={noop} />);
    // Default filter is "all" — category chips hidden.
    expect(screen.queryByRole("tab", { name: "Character" })).toBeNull();
  });

  it("shows category chips when kind = image", () => {
    render(<AssetLibrary nodes={[charImg]} open onToggle={noop} />);
    fireEvent.click(screen.getByRole("tab", { name: /image/i }));
    expect(screen.getByRole("tab", { name: "Character" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Scene" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Prop" })).toBeInTheDocument();
  });

  it("filters to only Character images when that chip is selected", () => {
    render(<AssetLibrary nodes={[charImg, sceneImg, propImg, untagged]} open onToggle={noop} />);
    fireEvent.click(screen.getByRole("tab", { name: /image/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Character" }));
    expect(screen.getByText("warrior")).toBeInTheDocument();
    expect(screen.queryByText("alley")).toBeNull();
    expect(screen.queryByText("sword")).toBeNull();
    expect(screen.queryByText("misc")).toBeNull();
  });
});

describe("AssetLibrary — category cycle pill", () => {
  it("cycles null → character on first click", () => {
    const onCycle = vi.fn();
    render(<AssetLibrary nodes={[img("misc")]} open onToggle={noop} onCycleCategory={onCycle} />);
    // Pill shows for image cards. Click it.
    const pill = screen.getByLabelText(/Set category — current none/i);
    fireEvent.click(pill);
    expect(onCycle).toHaveBeenCalledWith("misc", "character");
  });

  it("cycles character → scene", () => {
    const onCycle = vi.fn();
    const node = img("warrior", { data: { category: "character" } });
    render(<AssetLibrary nodes={[node]} open onToggle={noop} onCycleCategory={onCycle} />);
    const pill = screen.getByLabelText(/Set category — current character/i);
    fireEvent.click(pill);
    expect(onCycle).toHaveBeenCalledWith("warrior", "scene");
  });

  // P2 (D'): the cycle now includes "style" between prop and null:
  //   null → character → scene → prop → style → null
  it("cycles prop → style", () => {
    const onCycle = vi.fn();
    const node = img("sword", { data: { category: "prop" } });
    render(<AssetLibrary nodes={[node]} open onToggle={noop} onCycleCategory={onCycle} />);
    const pill = screen.getByLabelText(/Set category — current prop/i);
    fireEvent.click(pill);
    expect(onCycle).toHaveBeenCalledWith("sword", "style");
  });

  it("cycles style → null (back to untagged)", () => {
    const onCycle = vi.fn();
    const node = img("dust-look", { data: { category: "style" } });
    render(<AssetLibrary nodes={[node]} open onToggle={noop} onCycleCategory={onCycle} />);
    const pill = screen.getByLabelText(/Set category — current style/i);
    fireEvent.click(pill);
    expect(onCycle).toHaveBeenCalledWith("dust-look", null);
  });
});

describe("AssetLibrary — audio role badge (P2 D')", () => {
  function audio(id: string, data?: Record<string, unknown>) {
    return {
      id,
      project_id: "p1",
      type: "audio",
      title: id,
      prompt: "",
      status: "completed",
      x: 0,
      y: 0,
      width: 220,
      height: 56,
      media_urls: [`uploads/${id}.mp3`],
      data: data ?? {},
      created_by: "user",
      created_at: 1,
      updated_at: 1,
    } as unknown as Parameters<typeof AssetLibrary>[0]["nodes"][number];
  }

  it("renders the role badge when data.audio_role is set", () => {
    const sfx = audio("explosion", { audio_role: "sfx" });
    const music = audio("score", { audio_role: "music" });
    const voice = audio("narration", { audio_role: "voice" });
    render(<AssetLibrary nodes={[sfx, music, voice]} open onToggle={noop} />);
    expect(screen.getByLabelText(/audio role: sfx/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/audio role: music/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/audio role: voice/i)).toBeInTheDocument();
  });

  it("hides the role badge when audio_role is missing or unknown", () => {
    const bare = audio("untagged");
    const garbage = audio("weird", { audio_role: "explosion" });
    render(<AssetLibrary nodes={[bare, garbage]} open onToggle={noop} />);
    expect(screen.queryByLabelText(/audio role/i)).toBeNull();
  });
});
