import { zhCN } from "./zh-CN";

function createPlaceholderDictionary<T>(source: T, path: string[] = []): T {
  if (Array.isArray(source)) {
    return source.map((item, index) => createPlaceholderDictionary(item, [...path, String(index)])) as T;
  }

  if (typeof source === "function") {
    return (((..._args: unknown[]) => `[TODO en-US] ${path.join(".")}`) as unknown) as T;
  }

  if (source && typeof source === "object") {
    const entries = Object.entries(source as Record<string, unknown>).map(([key, value]) => [
      key,
      createPlaceholderDictionary(value, [...path, key]),
    ]);
    return Object.fromEntries(entries) as T;
  }

  if (typeof source === "string") {
    return (`[TODO en-US] ${path.join(".")}` as unknown) as T;
  }

  return source;
}

// Skeleton placeholder locale: keep the key structure aligned with zh-CN first,
// then backfill real English copy incrementally.
export const enUS = createPlaceholderDictionary(zhCN);
