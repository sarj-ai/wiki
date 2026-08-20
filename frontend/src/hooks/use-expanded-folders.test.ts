// @vitest-environment happy-dom

import {
  consumeRevealFor,
  expandPath,
  requestReveal,
  resetExpansionState,
} from "@/hooks/use-expanded-folders";
import {
  clearUserBrowserStorage,
  collapsedDrivesStorageKey,
  expandedFoldersStorageKey,
} from "@/lib/browser-storage";
import { installLocalStorage } from "@/test-stubs/local-storage";

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
    expect(window.localStorage.getItem(collapsedDrivesStorageKey("user-a"))).not.toBeNull();
  });

  it("does not persist when authenticated identity is unavailable", () => {
    expandPath(null, "drive-a", ["folder-a"]);

    expect(window.localStorage.length).toBe(0);
  });

  it("resets persisted, in-memory, and pending reveal state on termination", () => {
    expandPath("user-a", "drive-a", ["folder-a"]);
    requestReveal("private-file");

    resetExpansionState("user-a");
    clearUserBrowserStorage("user-a");

    expect(window.localStorage.getItem(expandedFoldersStorageKey("user-a"))).toBeNull();
    expect(window.localStorage.getItem(collapsedDrivesStorageKey("user-a"))).toBeNull();
    expect(consumeRevealFor("private-file")).toBe(false);
  });
});
