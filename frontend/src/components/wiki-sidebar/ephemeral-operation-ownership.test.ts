import { useDragOverStore } from "./use-drag-over-store";
import { useInFlightMovesStore } from "./use-in-flight-moves";

afterEach(() => {
  useDragOverStore.getState().reset();
  useInFlightMovesStore.getState().reset();
  vi.restoreAllMocks();
});

describe("ephemeral sidebar operation leases", () => {
  it("does not let an old same-file lease finish the current move", () => {
    const old = new AbortController();
    const current = new AbortController();
    const oldRemove = vi.spyOn(old.signal, "removeEventListener");
    const currentRemove = vi.spyOn(current.signal, "removeEventListener");
    const store = useInFlightMovesStore.getState();
    const oldOwner = store.start("shared-file", old.signal);
    const currentOwner = store.start("shared-file", current.signal);
    if (oldOwner === null || currentOwner === null) throw new Error("Expected current move leases");

    expect(oldRemove).toHaveBeenCalledOnce();
    store.end("shared-file", oldOwner);
    old.abort();
    expect(useInFlightMovesStore.getState().ids.has("shared-file")).toBe(true);
    expect(currentRemove).not.toHaveBeenCalled();

    store.end("shared-file", currentOwner);
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    expect(currentRemove).toHaveBeenCalledOnce();
  });

  it("releases every listener on reset and rejects a retired move without subscribing", () => {
    const session = new AbortController();
    const add = vi.spyOn(session.signal, "addEventListener");
    const remove = vi.spyOn(session.signal, "removeEventListener");
    const store = useInFlightMovesStore.getState();
    store.start("first", session.signal);
    store.start("second", session.signal);
    store.reset();
    expect(remove).toHaveBeenCalledOnce();
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    session.abort();
    expect(store.start("retired", session.signal)).toBeNull();
    expect(add).toHaveBeenCalledOnce();
  });

  it("lets provider retirement release its move even without explicit session clear", () => {
    const session = new AbortController();
    const remove = vi.spyOn(session.signal, "removeEventListener");
    useInFlightMovesStore.getState().start("file", session.signal);
    session.abort();
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
  });

  it("rejects a second same-session file move and admits retry once the first settles", () => {
    const session = new AbortController();
    const store = useInFlightMovesStore.getState();
    const owner = store.start("file", session.signal);
    if (!owner) throw new Error("Expected first admission");
    expect(store.start("file", session.signal)).toBeNull();
    expect(useInFlightMovesStore.getState().ids.has("file")).toBe(true);
    store.end("file", owner);
    const retry = store.start("file", session.signal);
    expect(retry).toBeTypeOf("symbol");
  });

  it("retires a thousand same-session moves with one listener and one publication", () => {
    const session = new AbortController();
    const add = vi.spyOn(session.signal, "addEventListener");
    const remove = vi.spyOn(session.signal, "removeEventListener");
    const store = useInFlightMovesStore.getState();
    for (let index = 0; index < 1_000; index++) store.start(`file-${index}`, session.signal);
    const published = vi.fn<() => void>();
    const release = useInFlightMovesStore.subscribe(published);
    session.abort();
    expect(add).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(published).toHaveBeenCalledOnce();
    expect(useInFlightMovesStore.getState().ids.size).toBe(0);
    release();
  });

  it("ignores old drag updates and finalizers while releasing their listeners", () => {
    const old = new AbortController();
    const current = new AbortController();
    const oldRemove = vi.spyOn(old.signal, "removeEventListener");
    const currentRemove = vi.spyOn(current.signal, "removeEventListener");
    const store = useDragOverStore.getState();
    const oldOwner = store.setActive({ containerId: "parent:a", id: "old" }, old.signal);
    const currentOwner = store.setActive(
      { containerId: "parent:b", id: "current" },
      current.signal,
    );
    if (oldOwner === null || currentOwner === null) throw new Error("Expected current drag leases");

    store.setOver({ containerId: "parent:b", id: "destination", intent: "into" }, currentOwner);
    store.setOver({ containerId: "parent:a", id: "old-destination", intent: "above" }, oldOwner);
    store.reset(oldOwner);
    old.abort();
    expect(oldRemove).toHaveBeenCalledOnce();
    expect(useDragOverStore.getState()).toMatchObject({
      activeId: "current",
      overId: "destination",
    });
    expect(currentRemove).not.toHaveBeenCalled();

    current.abort();
    expect(useDragOverStore.getState()).toMatchObject({ activeId: null, overId: null });
    expect(currentRemove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(store.setActive({ containerId: "parent:b", id: "retired" }, current.signal)).toBeNull();
  });
});
