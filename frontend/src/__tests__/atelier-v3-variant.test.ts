import { describe, it, expect } from "vitest";
import { resolveAtelierVariant } from "@/components/atelier/v3/useAtelierVariant";

describe("resolveAtelierVariant", () => {
  it("returns 'v3' when search param atelier=v3", () => {
    expect(resolveAtelierVariant("?atelier=v3")).toBe("v3");
  });
  it("returns 'legacy' otherwise", () => {
    expect(resolveAtelierVariant("?")).toBe("legacy");
    expect(resolveAtelierVariant("?foo=bar")).toBe("legacy");
    expect(resolveAtelierVariant("")).toBe("legacy");
  });
  it("does not throw on malformed input", () => {
    expect(() => resolveAtelierVariant("???x=%%")).not.toThrow();
    expect(resolveAtelierVariant("???x=%%")).toBe("legacy");
  });
});
