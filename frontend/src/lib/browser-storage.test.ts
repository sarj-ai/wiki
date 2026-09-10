// @vitest-environment happy-dom

import {
  clearUserBrowserStorage,
  collapsedDrivesStorageKey,
  expandedDrivesStorageKey,
  expandedFoldersStorageKey,
  purgeLegacyBrowserStorage,
  readActiveBrowserUser,
  recentAliasesStorageKey,
  recentDocsStorageKey,
  storeLoginRedirect,
  takeLoginRedirect,
  writeActiveBrowserUser,
} from "@/lib/browser-storage";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

describe("browser metadata storage", () => {
  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("uses disjoint keys for every immutable user id", () => {
    expect(recentDocsStorageKey("user-a")).not.toBe(recentDocsStorageKey("user-b"));
    expect(expandedFoldersStorageKey("user-a")).not.toBe(expandedFoldersStorageKey("user-b"));
    expect(expandedDrivesStorageKey("user-a")).not.toBe(expandedDrivesStorageKey("user-b"));
    expect(collapsedDrivesStorageKey("user-a")).not.toBe(collapsedDrivesStorageKey("user-b"));
    expect(recentAliasesStorageKey("user-a")).not.toBe(recentAliasesStorageKey("user-b"));
  });

  it("clears only the active user's metadata and preserves presentation settings", () => {
    const userAKeys = [
      recentDocsStorageKey("user-a"),
      recentAliasesStorageKey("user-a"),
      expandedFoldersStorageKey("user-a"),
      expandedDrivesStorageKey("user-a"),
      collapsedDrivesStorageKey("user-a"),
    ];
    for (const key of userAKeys) window.localStorage.setItem(key, "private-a");
    window.localStorage.setItem(recentDocsStorageKey("user-b"), "private-b");
    window.localStorage.setItem("theme", "dark");
    writeActiveBrowserUser("user-a");
    storeLoginRedirect("/d/private-a");

    clearUserBrowserStorage("user-a");

    for (const key of userAKeys) expect(window.localStorage.getItem(key)).toBeNull();
    expect(window.localStorage.getItem(recentDocsStorageKey("user-b"))).toBe("private-b");
    expect(window.localStorage.getItem("theme")).toBe("dark");
    expect(readActiveBrowserUser()).toBeNull();
    expect(takeLoginRedirect()).toBeNull();
  });

  it("deletes unowned legacy metadata without touching unrelated keys", () => {
    for (const key of [
      "sarj-wiki-query-cache",
      "sarj-wiki:recent-docs",
      "wiki-collapsed-drives",
      "wiki-expanded-folders",
    ]) {
      window.localStorage.setItem(key, "legacy-private-data");
    }
    window.localStorage.setItem("sarj-wiki:sidebar-width", "320px");

    purgeLegacyBrowserStorage();

    expect(window.localStorage.length).toBe(1);
    expect(window.localStorage.getItem("sarj-wiki:sidebar-width")).toBe("320px");
  });

  it("stores the one-shot login destination in session storage", () => {
    storeLoginRedirect("/d/document-a");

    expect(window.localStorage.getItem("sarj-redirect")).toBeNull();
    expect(takeLoginRedirect()).toBe("/d/document-a");
    expect(takeLoginRedirect()).toBeNull();
  });
});
