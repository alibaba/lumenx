// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Composer, type ComposerSubmitPayload } from "@/components/atelier/v3/Composer";
import { CapabilityIcon } from "@/components/atelier/v3/Composer/CapabilityIcon";

describe("Composer", () => {
  it("renders all 7 generation tabs", () => {
    render(<Composer activeTab="I2V" />);
    ["T2I","I2I","T2V","I2V","R2V","V2V","Audio"].forEach(t => {
      expect(screen.getByRole("tab", { name: t })).toBeInTheDocument();
    });
  });

  it("marks the active tab as aria-selected", () => {
    render(<Composer activeTab="R2V" />);
    expect(screen.getByRole("tab", { name: "R2V" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "I2V" })).toHaveAttribute("aria-selected", "false");
  });

  it("calls onTabChange when a different tab is clicked", () => {
    const onTabChange = vi.fn();
    render(<Composer activeTab="I2V" onTabChange={onTabChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "V2V" }));
    expect(onTabChange).toHaveBeenCalledWith("V2V");
  });

  it("renders capability-mismatch banner with role=alert when flagged", () => {
    render(<Composer activeTab="I2V" showCapabilityMismatch modelLabel="Wan 2.7" />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/Wan 2\.7/);
  });

  it("disables submit when capability mismatch", () => {
    render(<Composer activeTab="I2V" showCapabilityMismatch />);
    expect(screen.getByLabelText("Submit")).toBeDisabled();
  });

  it("calls onSubmit with all current chip values", () => {
    const onSubmit = vi.fn<(p: ComposerSubmitPayload) => void>();
    render(<Composer activeTab="I2V" prompt="hello" modelLabel="Wan 2.7"
                     aspect="16:9 · 720p" duration="5s" count="4×"
                     refs={[{ src: "a.jpg", role: "ref" }]}
                     onSubmit={onSubmit} />);
    fireEvent.click(screen.getByLabelText("Submit"));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      tab: "I2V", prompt: "hello", modelLabel: "Wan 2.7", aspect: "16:9 · 720p",
      duration: "5s", count: "4×", refs: [{ src: "a.jpg", role: "ref" }],
    }));
  });

  it("submits the user-typed prompt, not the initial prop", () => {
    const onSubmit = vi.fn();
    render(<Composer activeTab="I2V" prompt="" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "actual user input" } });
    fireEvent.click(screen.getByLabelText("Submit"));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ prompt: "actual user input" }));
  });

  it("syncs draft when prompt prop changes", () => {
    const { rerender } = render(<Composer activeTab="I2V" prompt="first" />);
    expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe("first");
    rerender(<Composer activeTab="I2V" prompt="second" />);
    expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe("second");
  });

  it("ArrowRight on active tab activates the next tab", () => {
    const onTabChange = vi.fn();
    render(<Composer activeTab="I2V" onTabChange={onTabChange} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "I2V" }), { key: "ArrowRight" });
    expect(onTabChange).toHaveBeenCalledWith("R2V");
  });

  it("ArrowLeft on first tab wraps to the last (Audio)", () => {
    const onTabChange = vi.fn();
    render(<Composer activeTab="T2I" onTabChange={onTabChange} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "T2I" }), { key: "ArrowLeft" });
    expect(onTabChange).toHaveBeenCalledWith("Audio");
  });

  it("Home/End jump to first/last tab", () => {
    const onTabChange = vi.fn();
    render(<Composer activeTab="I2V" onTabChange={onTabChange} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "I2V" }), { key: "End" });
    expect(onTabChange).toHaveBeenCalledWith("Audio");
    onTabChange.mockClear();
    fireEvent.keyDown(screen.getByRole("tab", { name: "I2V" }), { key: "Home" });
    expect(onTabChange).toHaveBeenCalledWith("T2I");
  });

  it("inactive tabs are tabIndex=-1 and active is 0", () => {
    render(<Composer activeTab="R2V" />);
    expect(screen.getByRole("tab", { name: "R2V" })).toHaveAttribute("tabIndex", "0");
    expect(screen.getByRole("tab", { name: "T2I" })).toHaveAttribute("tabIndex", "-1");
  });

  it("calls onClose when X is clicked", () => {
    const onClose = vi.fn();
    render(<Composer activeTab="I2V" onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("Close composer"));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders one Reference n image per ref + the Add button", () => {
    render(<Composer activeTab="I2V" refs={[{src:"a"},{src:"b"}]} />);
    expect(screen.getByAltText("Reference 1")).toBeInTheDocument();
    expect(screen.getByAltText("Reference 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Add reference")).toBeInTheDocument();
  });

  it("computes left/top from anchor + viewport when style not provided", () => {
    const { container } = render(
      <Composer activeTab="I2V"
                anchor={{ x: 64, y: 264, width: 240, height: 110 }}
                viewport={{ width: 1440, height: 900, rightRailWidth: 396 }} />
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.left).toBe("64px");
    expect(root.style.top).toBe(`${264 + 110 + 16}px`);
  });

  it("explicit style prop overrides anchor positioning", () => {
    const { container } = render(<Composer activeTab="I2V" style={{ left: 999, top: 99 }}
                                            anchor={{ x: 0, y: 0, width: 0, height: 0 }}
                                            viewport={{ width: 1440, height: 900, rightRailWidth: 396 }} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.left).toBe("999px");
    expect(root.style.top).toBe("99px");
  });
});

describe("CapabilityIcon", () => {
  it("renders supported state with emerald + check", () => {
    const { container } = render(<CapabilityIcon on label="img-ref" sym="🖼" />);
    expect(container.firstElementChild?.className).toMatch(/emerald/);
  });

  it("renders unsupported state with red + X", () => {
    const { container } = render(<CapabilityIcon on={false} label="vid-ref" sym="🎞" />);
    expect(container.firstElementChild?.className).toMatch(/red/);
  });

  it("aria-label reflects support", () => {
    render(<CapabilityIcon on={false} label="ff" sym="🅵" />);
    expect(screen.getByRole("img", { name: /ff not supported/i })).toBeInTheDocument();
  });
});

// ── Capability mismatch (catalog-derived) ──────────────────────────────
//
// The Composer derives mismatch state from `modelLabel` + ref `kind`s using
// the model catalog. The override prop wins when explicitly set; otherwise
// the catalog answers. Rules under test mirror modelCatalog.validateAtelierRefs.

describe("Composer capability mismatch (auto)", () => {
  it("shows banner when refs exceed model max", () => {
    // Kling V3 I2V accepts at most 1 reference image.
    render(
      <Composer
        activeTab="I2V"
        modelLabel="Kling V3 I2V"
        refs={[
          { src: "a.jpg", kind: "image" },
          { src: "b.jpg", kind: "image" },
        ]}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/Kling V3 I2V/);
    expect(alert.textContent).toMatch(/at most 1 reference/);
    expect(screen.getByLabelText("Submit")).toBeDisabled();
  });

  it("shows banner when video ref attached to image-only model", () => {
    render(
      <Composer
        activeTab="I2V"
        modelLabel="Kling V3 I2V"
        refs={[{ src: "v.mp4", kind: "video" }]}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/video references/);
  });

  it("no banner when refs satisfy catalog constraint", () => {
    render(
      <Composer
        activeTab="I2V"
        modelLabel="Kling V3 I2V"
        refs={[{ src: "a.jpg", kind: "image" }]}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("Submit")).not.toBeDisabled();
  });

  it("no banner when model unknown to catalog (fail-open)", () => {
    render(
      <Composer
        activeTab="I2V"
        modelLabel="Wan 2.7"
        refs={[{ src: "a.jpg", kind: "image" }]}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("explicit prop override forces banner even without violations", () => {
    render(
      <Composer
        activeTab="I2V"
        modelLabel="Kling V3 I2V"
        showCapabilityMismatch
        refs={[{ src: "a.jpg", kind: "image" }]}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("explicit prop override=false suppresses catalog-derived mismatch", () => {
    render(
      <Composer
        activeTab="I2V"
        modelLabel="Kling V3 I2V"
        showCapabilityMismatch={false}
        refs={[{ src: "v.mp4", kind: "video" }]}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
