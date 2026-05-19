// @vitest-environment happy-dom
//
// MiniMarkdown is the in-house tiny inline-markdown renderer used for
// project descriptions and other short Atelier text fields. We don't
// pull in react-markdown for this — these tests lock the supported
// subset (**bold** / *italic* / `code` / [link]) and prove the unsafe
// schemes are rejected.
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { MiniMarkdown } from "@/components/atelier/v3/MiniMarkdown";

describe("MiniMarkdown", () => {
  it("renders plain text in a single paragraph", () => {
    const { container } = render(<MiniMarkdown source="hello world" />);
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.textContent).toBe("hello world");
  });

  it("emits bold for **...**", () => {
    const { container } = render(<MiniMarkdown source="say **hi** there" />);
    const strong = container.querySelector("strong");
    expect(strong?.textContent).toBe("hi");
  });

  it("emits italic for *...*", () => {
    const { container } = render(<MiniMarkdown source="say *hi* there" />);
    const em = container.querySelector("em");
    expect(em?.textContent).toBe("hi");
  });

  it("does not double-parse a bold mark as italic", () => {
    const { container } = render(<MiniMarkdown source="**hi**" />);
    expect(container.querySelector("strong")?.textContent).toBe("hi");
    expect(container.querySelector("em")).toBeNull();
  });

  it("emits inline code for `...`", () => {
    const { container } = render(<MiniMarkdown source="run `npm test`" />);
    const code = container.querySelector("code");
    expect(code?.textContent).toBe("npm test");
  });

  it("emits a link for [text](url)", () => {
    const { container } = render(
      <MiniMarkdown source="see [docs](https://example.com)" />,
    );
    const a = container.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com");
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toContain("noopener");
    expect(a?.textContent).toBe("docs");
  });

  it("rejects javascript: links — renders as inert text", () => {
    const { container } = render(
      <MiniMarkdown source="bad [click](javascript:alert(1))" />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("click");
  });

  it("rejects data: links", () => {
    const { container } = render(
      <MiniMarkdown source="[click](data:text/html,<script>)" />,
    );
    expect(container.querySelector("a")).toBeNull();
  });

  it("treats blank-line gaps as paragraph breaks", () => {
    const { container } = render(<MiniMarkdown source={"first\n\nsecond"} />);
    expect(container.querySelectorAll("p")).toHaveLength(2);
  });

  it("treats single newlines as <br/>", () => {
    const { container } = render(<MiniMarkdown source={"line one\nline two"} />);
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelectorAll("br")).toHaveLength(1);
  });

  it("never emits raw HTML — script tags become text", () => {
    const { container } = render(
      <MiniMarkdown source="hello <script>boom</script>" />,
    );
    // No <script> element rendered — the text shows literal angle brackets.
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  it("does not emit a bold element when the closing ** is missing", () => {
    // The italic-fallback path may consume the `**` as empty italic, but
    // the important contract is: no <strong> with malformed input, and
    // the trailing text survives intact.
    const { container } = render(<MiniMarkdown source="this **never closes" />);
    expect(container.querySelector("strong")).toBeNull();
    expect(container.textContent).toContain("never closes");
  });
});
