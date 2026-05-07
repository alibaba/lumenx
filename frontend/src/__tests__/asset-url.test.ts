import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendAssetQueryParam,
  canAppendAssetQueryParams,
  getAssetFetchUrl,
  getAssetUrl,
  getAssetUrlWithTimestamp,
  isDirectAssetPath,
  isPresignedAssetUrl,
  stripAssetApiPrefix,
} from "../lib/utils";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("asset url helpers", () => {
  it("appends timestamp to local asset paths", () => {
    expect(getAssetUrlWithTimestamp("output/frame.png", 123)).toBe(
      "http://127.0.0.1:18177/files/output/frame.png?t=123",
    );
  });

  it("preserves TOS presigned urls without appending timestamp or retry params", () => {
    const presignedUrl =
      "https://tos-cn-beijing.volces.com/seedance-inputs/frame.png?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=test&X-Tos-Signature=abc123";

    expect(isPresignedAssetUrl(presignedUrl)).toBe(true);
    expect(canAppendAssetQueryParams(presignedUrl)).toBe(false);
    expect(getAssetUrlWithTimestamp(presignedUrl, 456)).toBe(presignedUrl);
    expect(appendAssetQueryParam(presignedUrl, "retry", 1)).toBe(presignedUrl);
  });

  it("still appends retry params for normal public urls", () => {
    expect(appendAssetQueryParam("https://cdn.example.com/frame.png", "retry", 2)).toBe(
      "https://cdn.example.com/frame.png?retry=2",
    );
  });

  it("does not mutate blob urls", () => {
    expect(appendAssetQueryParam("blob:test-asset", "retry", 1)).toBe("blob:test-asset");
  });

  it("detects direct asset paths", () => {
    expect(isDirectAssetPath("https://example.com/file.png")).toBe(true);
    expect(isDirectAssetPath("blob:test-asset")).toBe(true);
    expect(isDirectAssetPath("output/frame.png")).toBe(false);
  });

  it("uses dev proxy only for local asset fetches", () => {
    vi.stubEnv("NODE_ENV", "development");

    expect(getAssetFetchUrl("output/video.mp4")).toBe("/api-proxy/files/output/video.mp4");
    expect(getAssetFetchUrl("/files/output/video.mp4")).toBe("/api-proxy/files/output/video.mp4");

    const presignedUrl =
      "https://tos-cn-beijing.volces.com/seedance-inputs/video.mp4?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Signature=abc123";
    expect(getAssetFetchUrl(presignedUrl)).toBe(presignedUrl);
  });

  it("strips local api file prefixes but preserves remote urls", () => {
    const localAssetUrl = getAssetUrl("output/frame.png");
    expect(stripAssetApiPrefix(localAssetUrl)).toBe("output/frame.png");
    expect(stripAssetApiPrefix("/files/output/frame.png")).toBe("output/frame.png");

    const presignedUrl =
      "https://tos-cn-beijing.volces.com/seedance-inputs/frame.png?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Signature=abc123";
    expect(stripAssetApiPrefix(presignedUrl)).toBe(presignedUrl);
  });
});
