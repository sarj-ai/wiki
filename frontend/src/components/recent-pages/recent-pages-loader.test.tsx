import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { RecentPagesLoader } from "@/components/recent-pages/recent-pages-loader";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { saveLastDoc } from "@/lib/last-doc";
import type { WIKI_READ_API } from "@/lib/wiki-read-api";
import { installLocalStorage } from "@/test-stubs/install-local-storage";
import { MIME_FORM } from "@/types/wiki";

const getRecentFiles = vi.hoisted(() => vi.fn<typeof WIKI_READ_API.getRecentFiles>());
const documentIntent = vi.hoisted(() => vi.fn(() => ({})));
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getRecentFiles } }));
vi.mock("@/components/wiki-sidebar/use-document-intent", () => ({
  useDocumentIntent: documentIntent,
}));

describe("RecentPagesLoader", () => {
  let root: Root;
  let host: HTMLDivElement;

  beforeEach(() => {
    installLocalStorage();
    localStorage.clear();
    getRecentFiles.mockReset();
    documentIntent.mockClear();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    vi.restoreAllMocks();
  });

  function mount(userId: null | string) {
    flushSync(() =>
      root.render(
        <BrowserUserContext value={userId}>
          <RecentPagesLoader />
        </BrowserUserContext>,
      ),
    );
  }

  it("shows an empty landing view without making a history request", async () => {
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
    expect(getRecentFiles).not.toHaveBeenCalled();
  });

  it("waits for a verified user and never displays locally saved titles", async () => {
    saveLastDoc("user-a", { fileId: "file-1", title: "Private old title", mimeType: "text/plain" });
    const pending = Promise.withResolvers<[]>();
    getRecentFiles.mockReturnValue(pending.promise);
    mount(null);
    expect(getRecentFiles).not.toHaveBeenCalled();
    mount("user-a");
    expect(host).toHaveTextContent("Loading recent pages");
    expect(host).not.toHaveTextContent("Private old title");
    pending.resolve([]);
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
  });

  it("renders five freshly authorized pages in most-recent order with working links", async () => {
    for (let index = 0; index < 6; index += 1) {
      saveLastDoc("user-a", {
        fileId: `file-${index}`,
        title: `Stale ${index}`,
        mimeType: "text/plain",
      });
    }
    getRecentFiles.mockResolvedValue(
      Array.from({ length: 5 }, (_, index) => ({
        id: `file-${5 - index}`,
        name: `Fresh ${5 - index}`,
        mimeType: "text/plain",
        driveName: "Engineering",
      })),
    );
    mount("user-a");
    await vi.waitFor(() => expect(host.querySelectorAll("a")).toHaveLength(5));
    expect(getRecentFiles.mock.calls[0]?.[0]).toEqual([
      "file-5",
      "file-4",
      "file-3",
      "file-2",
      "file-1",
      "file-0",
    ]);
    expect([...host.querySelectorAll("a")].map((link) => link.getAttribute("href"))).toEqual([
      "/d/file-5",
      "/d/file-4",
      "/d/file-3",
      "/d/file-2",
      "/d/file-1",
    ]);
    expect(host).not.toHaveTextContent("Stale");
    expect(host).toHaveTextContent("Engineering");
    expect(getRecentFiles).toHaveBeenCalledOnce();
  });

  it("aborts obsolete reads and requires fresh authorization across A to B to A", async () => {
    saveLastDoc("user-a", { fileId: "file-a", title: "Old A", mimeType: "text/plain" });
    saveLastDoc("user-b", { fileId: "file-b", title: "Old B", mimeType: "text/plain" });
    const pending =
      Promise.withResolvers<{ id: string; name: string; mimeType: string; driveName: null }[]>();
    getRecentFiles
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    mount("user-a");
    const signal = getRecentFiles.mock.calls[0]?.[1];
    mount("user-b");
    expect(signal?.aborted).toBe(true);
    mount("user-a");
    pending.resolve([
      { id: "file-a", name: "Late private A", mimeType: "text/plain", driveName: null },
    ]);
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
    expect(host).not.toHaveTextContent("Late private A");
    expect(getRecentFiles).toHaveBeenCalledTimes(3);
  });

  it("opens a freshly authorized Google Form externally without document speculation", async () => {
    saveLastDoc("user-a", { fileId: "form-1", title: "Old form", mimeType: MIME_FORM });
    getRecentFiles.mockResolvedValue([
      { id: "form-1", name: "Current form", mimeType: MIME_FORM, driveName: "Engineering" },
    ]);
    mount("user-a");
    await vi.waitFor(() => expect(host.querySelector("a")).toHaveTextContent("Current form"));
    const link = host.querySelector("a");
    expect(link).toHaveAttribute("href", "https://docs.google.com/forms/d/form-1/edit");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("title", "Current form — opens in a new tab");
    expect(documentIntent).toHaveBeenCalledWith(
      expect.objectContaining({ disabled: true, fileId: "form-1", mimeType: MIME_FORM }),
    );
  });

  it("offers a fresh retry after a failed read and cancels it on unmount", async () => {
    saveLastDoc("user-a", { fileId: "file-a", title: "Old title", mimeType: "text/plain" });
    getRecentFiles.mockRejectedValueOnce(new Error("Unavailable"));
    const retry = Promise.withResolvers<[]>();
    getRecentFiles.mockReturnValueOnce(retry.promise);
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages could not be loaded"));
    const button = host.querySelector("button");
    expect(button).not.toBeNull();
    if (!button) throw new Error("Retry button missing");
    flushSync(() => button.click());
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
    const signal = getRecentFiles.mock.calls[1]?.[1];
    flushSync(() => root.render(null));
    expect(signal?.aborted).toBe(true);
    retry.resolve([]);
    await retry.promise;
    expect(host).toBeEmptyDOMElement();
  });
});
