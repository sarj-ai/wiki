import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useDocViewerStore, viewerIdentity } from "@/components/wiki-document/doc-viewer-store";
import { MIME_DOC } from "@/types/wiki";

const doc = (fileId: string, reloadKey = 0, title = fileId) => ({
  fileId,
  mimeType: MIME_DOC,
  reloadKey,
  title,
});

describe("document viewer lifecycle", () => {
  beforeEach(() => {
    useDocViewerStore.getState().reset();
  });

  afterEach(() => {
    useDocViewerStore.getState().reset();
  });

  it("does not let stale cleanup clear the newly active document", () => {
    const store = useDocViewerStore.getState();
    const ownerA = Symbol("a");
    const ownerB = Symbol("b");
    store.setActive(doc("a"), ownerA);
    store.setActive(doc("b"), ownerB);
    store.clearActive(ownerA);

    expect(useDocViewerStore.getState().active?.fileId).toBe("b");
  });

  it("updates the active document and bounded pool atomically", () => {
    const store = useDocViewerStore.getState();
    const owner = Symbol("owner");
    store.setActive(doc("a"), owner);
    store.setActive(doc("b"), owner);
    store.setActive(doc("c"), owner);
    store.setActive(doc("d"), owner);

    const state = useDocViewerStore.getState();
    expect(state.active?.fileId).toBe("d");
    expect(state.pool.map((entry) => entry.fileId)).toEqual(["d", "c", "b"]);
  });

  it("keeps a replacement slot when the prior ref is cleared", () => {
    const first = document.createElement("div");
    const second = document.createElement("div");
    const store = useDocViewerStore.getState();
    const firstOwner = Symbol("first");
    const secondOwner = Symbol("second");
    store.setSlot(first, firstOwner);
    store.setSlot(second, secondOwner);
    store.setSlot(null, firstOwner);

    expect(useDocViewerStore.getState().slot).toBe(second);
  });

  it("does not let an old same-file instance clear its replacement", () => {
    const store = useDocViewerStore.getState();
    const firstOwner = Symbol("first");
    const secondOwner = Symbol("second");
    store.setActive(doc("a"), firstOwner);
    store.setActive(doc("a"), secondOwner);
    store.clearActive(firstOwner);

    expect(useDocViewerStore.getState().active?.fileId).toBe("a");
  });

  it("refreshes title metadata without changing viewer identity", () => {
    const store = useDocViewerStore.getState();
    const owner = Symbol("owner");
    store.setActive(doc("a", 0, "Old title"), owner);
    store.setActive(doc("a", 0, "New title"), owner);

    expect(useDocViewerStore.getState().pool[0]?.title).toBe("New title");
  });

  it("ignores stale iframe readiness and cleanup from an older owner", () => {
    const store = useDocViewerStore.getState();
    const identity = viewerIdentity(doc("a"));
    const oldOwner = Symbol("old");
    const currentOwner = Symbol("current");
    store.markViewerLoading(identity, oldOwner);
    store.markViewerLoading(identity, currentOwner);

    store.markViewerReady(identity, oldOwner);
    store.clearViewerLoad(identity, oldOwner);

    expect(useDocViewerStore.getState().viewerLoads[identity]).toEqual({
      owner: currentOwner,
      status: "loading",
    });
  });

  it("retains ready prewarmed viewer state when it becomes active", () => {
    const store = useDocViewerStore.getState();
    const entry = doc("a");
    const viewerOwner = Symbol("viewer");
    store.markViewerLoading(viewerIdentity(entry), viewerOwner);
    store.markViewerReady(viewerIdentity(entry), viewerOwner);
    store.setActive(entry, Symbol("active"));

    expect(useDocViewerStore.getState().viewerLoads[viewerIdentity(entry)]?.status).toBe("ready");
  });
});
