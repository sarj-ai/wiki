// @vitest-environment happy-dom

import { readRecentDocs, saveLastDoc } from "@/lib/last-doc";
import { installLocalStorage } from "@/test-stubs/local-storage";

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
});
