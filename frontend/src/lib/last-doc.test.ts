// @vitest-environment happy-dom

import { recentDocsStorageKey } from "@/lib/browser-storage";
import { readRecentDocs, saveLastDoc } from "@/lib/last-doc";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

describe("recent document warmup metadata", () => {
  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
  });

  it("is isolated by user and deduplicated in most-recent-first order", () => {
    saveLastDoc("user-a", { fileId: "file-1", mimeType: "text/plain", title: "First" });
    saveLastDoc("user-a", { fileId: "file-2", mimeType: "text/plain", title: "Second" });
    saveLastDoc("user-a", { fileId: "file-1", mimeType: "text/plain", title: "Renamed" });

    expect(readRecentDocs("user-a")).toEqual([
      { fileId: "file-1", mimeType: "text/plain", title: "Renamed" },
      { fileId: "file-2", mimeType: "text/plain", title: "Second" },
    ]);
    expect(readRecentDocs("user-b")).toEqual([]);
  });

  it("never inherits the unscoped legacy key", () => {
    window.localStorage.setItem(
      "sarj-wiki:recent-docs",
      JSON.stringify([{ fileId: "legacy-file", mimeType: "text/plain", title: "Legacy" }]),
    );

    expect(readRecentDocs("user-a")).toEqual([]);
  });

  it("keeps only six documents", () => {
    for (let index = 0; index < 8; index += 1) {
      saveLastDoc("user-a", {
        fileId: `file-${index}`,
        mimeType: "text/plain",
        title: `Document ${index}`,
      });
    }

    expect(readRecentDocs("user-a").map((doc) => doc.fileId)).toEqual([
      "file-7",
      "file-6",
      "file-5",
      "file-4",
      "file-3",
      "file-2",
    ]);
  });

  it("discards oversized stored history before admitting recent-page reads", () => {
    window.localStorage.setItem(
      recentDocsStorageKey("user-a"),
      JSON.stringify(
        Array.from({ length: 7 }, (_, index) => ({
          fileId: `file-${index}`,
          mimeType: "text/plain",
          title: "Page",
        })),
      ),
    );
    expect(readRecentDocs("user-a")).toEqual([]);
  });

  it("retains canonical recent bytes without repeating an identical storage write", () => {
    const doc = { fileId: "file-1", mimeType: "text/plain", title: "Page" };
    saveLastDoc("user-a", doc);
    const write = vi.spyOn(window.localStorage, "setItem");

    saveLastDoc("user-a", doc);
    expect(write).not.toHaveBeenCalled();

    saveLastDoc("user-a", { ...doc, title: "Renamed" });
    expect(write).toHaveBeenCalledOnce();
    expect(readRecentDocs("user-a")).toEqual([{ ...doc, title: "Renamed" }]);
  });

  it("keeps a write available when storage reads fail", () => {
    const doc = { fileId: "file-1", mimeType: "text/plain", title: "Page" };
    const read = vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("Storage read unavailable");
    });
    const write = vi.spyOn(window.localStorage, "setItem");
    saveLastDoc("user-a", doc);
    expect(write).toHaveBeenCalledExactlyOnceWith(
      recentDocsStorageKey("user-a"),
      JSON.stringify([doc]),
    );
    read.mockRestore();
    expect(readRecentDocs("user-a")).toEqual([doc]);
  });

  it("owns each invalid-history fallback across reads and users", () => {
    window.localStorage.setItem(recentDocsStorageKey("user-a"), JSON.stringify([{ fileId: "a" }]));
    window.localStorage.setItem(recentDocsStorageKey("user-b"), JSON.stringify([{ title: "b" }]));
    const discarded = readRecentDocs("user-a");
    discarded.push({ fileId: "poison", mimeType: "text/plain", title: "Private poison" });

    expect(readRecentDocs("user-a")).toEqual([]);
    expect(readRecentDocs("user-b")).toEqual([]);
    const current = { fileId: "current", mimeType: "text/plain", title: "Current" };
    saveLastDoc("user-a", current);
    expect(readRecentDocs("user-a")).toEqual([current]);
    expect(readRecentDocs("user-b")).toEqual([]);
  });
});
