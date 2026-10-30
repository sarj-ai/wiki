// @vitest-environment happy-dom

import {
  consumeRevealFor,
  expandPath,
  requestReveal,
  resetExpansionState,
  setDriveExpanded,
} from "@/hooks/use-expanded-folders";
import {
  clearUserBrowserStorage,
  collapsedDrivesStorageKey,
  expandedDrivesStorageKey,
  expandedFoldersStorageKey,
} from "@/lib/browser-storage";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

describe("per-user sidebar expansion", () => {
  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
  });

  it("persists Drive and folder ids only inside the immutable user's namespace", () => {
    expandPath("user-a", "drive-a", ["folder-a"]);

    expect(window.localStorage.getItem(expandedFoldersStorageKey("user-b"))).toBeNull();
    expect(window.localStorage.getItem(collapsedDrivesStorageKey("user-b"))).toBeNull();
    expect(window.localStorage.getItem(expandedFoldersStorageKey("user-a"))).toContain("folder-a");
    expect(window.localStorage.getItem(expandedDrivesStorageKey("user-a"))).toContain("drive-a");
  });

  it("does not persist when authenticated identity is unavailable", () => {
    expandPath(null, "drive-a", ["folder-a"]);

    expect(window.localStorage.length).toBe(0);
  });

  it("expands a breadcrumb with one folder persistence write and skips unchanged paths", () => {
    const writes = vi.spyOn(window.localStorage, "setItem");
    const folders = Array.from({ length: 10 }, (_, index) => `folder-${index}`);
    expandPath("user-batch", "drive-batch", folders);
    expect(writes).toHaveBeenCalledTimes(2);
    expect(
      writes.mock.calls.filter(([key]) => key === expandedFoldersStorageKey("user-batch")),
    ).toHaveLength(1);
    for (const id of folders) {
      expect(window.localStorage.getItem(expandedFoldersStorageKey("user-batch"))).toContain(id);
    }
    writes.mockClear();
    expandPath("user-batch", "drive-batch", [...folders, folders[0] ?? ""]);
    setDriveExpanded("user-batch", "drive-batch", true);
    setDriveExpanded("user-batch", "already-collapsed", false);
    expect(writes).not.toHaveBeenCalled();
    writes.mockRestore();
    resetExpansionState("user-batch");
  });

  it("resets persisted, in-memory, and pending reveal state on termination", () => {
    expandPath("user-a", "drive-a", ["folder-a"]);
    requestReveal("private-file");

    resetExpansionState("user-a");
    clearUserBrowserStorage("user-a");

    expect(window.localStorage.getItem(expandedFoldersStorageKey("user-a"))).toBeNull();
    expect(window.localStorage.getItem(expandedDrivesStorageKey("user-a"))).toBeNull();
    expect(window.localStorage.getItem(collapsedDrivesStorageKey("user-a"))).toBeNull();
    expect(consumeRevealFor("private-file")).toBe(false);
  });
});
