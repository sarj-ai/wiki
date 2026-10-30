import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
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

  it("consumes only the prewarm target that becomes active, in the same publication", () => {
    const store = useDocViewerStore.getState();
    const owner = Symbol("owner");
    store.setPrewarm(doc("a"));
    store.setActive(doc("b"), owner);
    expect(useDocViewerStore.getState().prewarm?.fileId).toBe("a");
    const publications: { active: string | undefined; prewarm: string | undefined }[] = [];
    const unsubscribe = useDocViewerStore.subscribe((state) => {
      publications.push({ active: state.active?.fileId, prewarm: state.prewarm?.fileId });
    });
    store.setActive(doc("a"), owner);
    unsubscribe();
    expect(publications).toEqual([{ active: "a", prewarm: undefined }]);
    expect(useDocViewerStore.getState().pool[0]?.fileId).toBe("a");
    store.setActive(doc("c"), owner);
    store.setActive(doc("d"), owner);
    store.setActive(doc("e"), owner);
    expect(useDocViewerStore.getState().pool.map((entry) => entry.fileId)).toEqual(["e", "d", "c"]);
    expect(useDocViewerStore.getState().prewarm).toBeNull();
  });

  it("does not revive a consumed target when its authorization finishes after activation", () => {
    const store = useDocViewerStore.getState();
    const owner = Symbol("owner");
    store.setActive(doc("a"), owner);
    store.setPrewarm(doc("a"));
    expect(useDocViewerStore.getState().prewarm).toBeNull();
    store.setPrewarm(doc("b"));
    store.setPrewarm(doc("a"));
    expect(useDocViewerStore.getState().prewarm?.fileId).toBe("b");
  });
});
