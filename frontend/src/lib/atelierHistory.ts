interface HistoryState<T> {
  undo: T[];
  redo: T[];
  busy: boolean;
  version: number;
}

export class AtelierHistory<T> {
  private readonly projects = new Map<string, HistoryState<T>>();

  constructor(private readonly limit = 50) {}

  state(projectId: string): HistoryState<T> {
    let state = this.projects.get(projectId);
    if (!state) {
      state = { undo: [], redo: [], busy: false, version: 0 };
      this.projects.set(projectId, state);
    }
    return state;
  }

  record(projectId: string, item: T): void {
    const state = this.state(projectId);
    state.undo.push(item);
    if (state.undo.length > this.limit) state.undo.shift();
    state.redo = [];
    state.version += 1;
  }

  undo(projectId: string, apply: (item: T) => Promise<void>): Promise<T | undefined> {
    return this.apply(projectId, "undo", apply);
  }

  redo(projectId: string, apply: (item: T) => Promise<void>): Promise<T | undefined> {
    return this.apply(projectId, "redo", apply);
  }

  private async apply(
    projectId: string,
    direction: "undo" | "redo",
    execute: (item: T) => Promise<void>,
  ): Promise<T | undefined> {
    const state = this.state(projectId);
    const source = state[direction];
    const item = source[source.length - 1];
    if (state.busy || source.length === 0) return undefined;
    const version = state.version;
    state.busy = true;
    try {
      await execute(item);
      const index = source.lastIndexOf(item);
      if (index !== -1) source.splice(index, 1);
      if (state.version === version) {
        state[direction === "undo" ? "redo" : "undo"].push(item);
      }
      return item;
    } finally {
      state.busy = false;
    }
  }
}
