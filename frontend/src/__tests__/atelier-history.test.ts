import { describe, expect, it, vi } from "vitest";
import { AtelierHistory } from "@/lib/atelierHistory";

function deferred() {
  let resolve!: () => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<void>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

describe("AtelierHistory", () => {
  it("keeps undo and redo scoped to the originating project", async () => {
    const history = new AtelierHistory<string>();
    history.record("a", "create-a");
    history.record("b", "create-b");
    const apply = vi.fn().mockResolvedValue(undefined);
    await history.undo("a", apply);
    expect(apply).toHaveBeenCalledWith("create-a");
    expect(history.state("a")).toMatchObject({ undo: [], redo: ["create-a"] });
    expect(history.state("b")).toMatchObject({ undo: ["create-b"], redo: [] });
  });

  it("advances history only after a successful operation", async () => {
    const history = new AtelierHistory<string>();
    history.record("a", "move");
    const operation = deferred();
    const pending = history.undo("a", () => operation.promise);
    expect(history.state("a")).toMatchObject({ undo: ["move"], redo: [], busy: true });
    operation.resolve();
    expect(await pending).toBe("move");
    expect(history.state("a")).toMatchObject({ undo: [], redo: ["move"], busy: false });
  });

  it("preserves a failed command for explicit retry", async () => {
    const history = new AtelierHistory<string>();
    history.record("a", "delete");
    await expect(history.undo("a", async () => { throw new Error("offline"); })).rejects.toThrow("offline");
    expect(history.state("a")).toMatchObject({ undo: ["delete"], redo: [], busy: false });
    await history.undo("a", async () => {});
    expect(history.state("a").redo).toEqual(["delete"]);
  });

  it("does not execute the same undo twice while it is in flight", async () => {
    const history = new AtelierHistory<string>();
    history.record("a", "move");
    const operation = deferred();
    const apply = vi.fn(() => operation.promise);
    const pending = history.undo("a", apply);
    expect(await history.undo("a", apply)).toBeUndefined();
    expect(apply).toHaveBeenCalledTimes(1);
    operation.resolve();
    await pending;
  });

  it("invalidates redo when a new edit happens during undo", async () => {
    const history = new AtelierHistory<string>();
    history.record("a", "old");
    const operation = deferred();
    const pending = history.undo("a", () => operation.promise);
    history.record("a", "new");
    operation.resolve();
    await pending;
    expect(history.state("a")).toMatchObject({ undo: ["new"], redo: [] });
  });

  it("bounds each project's history independently and redoes successfully", async () => {
    const history = new AtelierHistory<number>(2);
    history.record("a", 1);
    history.record("a", 2);
    history.record("a", 3);
    expect(history.state("a").undo).toEqual([2, 3]);
    await history.undo("a", async () => {});
    const apply = vi.fn().mockResolvedValue(undefined);
    expect(await history.redo("a", apply)).toBe(3);
    expect(apply).toHaveBeenCalledWith(3);
    expect(history.state("a")).toMatchObject({ undo: [2, 3], redo: [] });
  });
});
