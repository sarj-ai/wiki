import { MutationObserver, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { RecentPagesLoader } from "@/components/recent-pages/recent-pages-loader";
import {
  RecentPagesSessionProvider,
  useRecentPagesScope,
} from "@/components/recent-pages/recent-pages-session";
import { ApiError } from "@/lib/api-error";
import { recentDocsStorageKey } from "@/lib/browser-storage";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { saveLastDoc } from "@/lib/last-doc";
import { recentPagesQueryOptions, type RecentPagesScope } from "@/lib/recent-pages-query";
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
  let client: QueryClient;
  let scope: null | RecentPagesScope;

  beforeEach(() => {
    installLocalStorage();
    localStorage.clear();
    getRecentFiles.mockReset();
    documentIntent.mockClear();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    scope = null;
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    client.clear();
    vi.restoreAllMocks();
  });

  function mount(userId: null | string, landing = true) {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <BrowserUserContext value={userId}>
            <RecentPagesSessionProvider userId={userId}>
              <CaptureScope />
              {landing ? <RecentPagesLoader /> : <span>Document page</span>}
            </RecentPagesSessionProvider>
          </BrowserUserContext>
        </QueryClientProvider>,
      ),
    );
  }

  function CaptureScope() {
    scope = useRecentPagesScope();
    return null;
  }

  it("shows an empty landing view without making a history request", async () => {
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
    expect(getRecentFiles).not.toHaveBeenCalled();
  });

  it.each(["storage", "focus", "visibilitychange"])(
    "refreshes a mounted home from changed history after %s without rendering local titles",
    async (eventType) => {
      mount("user-a");
      await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
      saveLastDoc("user-a", {
        fileId: "new-page",
        title: "Untrusted local title",
        mimeType: "text/plain",
      });
      const pending =
        Promise.withResolvers<{ id: string; name: string; mimeType: string; driveName: null }[]>();
      getRecentFiles.mockReturnValueOnce(pending.promise);
      if (eventType === "visibilitychange") {
        vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
        flushSync(() => document.dispatchEvent(new Event("visibilitychange")));
      } else if (eventType === "storage") {
        flushSync(() =>
          window.dispatchEvent(
            new StorageEvent("storage", { key: recentDocsStorageKey("user-a") }),
          ),
        );
      } else {
        flushSync(() => window.dispatchEvent(new Event("focus")));
      }
      await vi.waitFor(() => expect(getRecentFiles).toHaveBeenCalledOnce());
      expect(getRecentFiles.mock.calls[0]?.[0]).toEqual(["new-page"]);
      expect(host).toHaveTextContent("Loading recent pages");
      expect(host).not.toHaveTextContent("Untrusted local title");
      pending.resolve([
        { id: "new-page", name: "Authorized new page", mimeType: "text/plain", driveName: null },
      ]);
      await vi.waitFor(() =>
        expect(host.querySelector('a[href="/d/new-page"]')).toHaveTextContent(
          "Authorized new page",
        ),
      );
    },
  );

  it("ignores another user's storage events and unchanged IDs, and detaches after unmount", async () => {
    getRecentFiles.mockResolvedValue([]);
    saveLastDoc("user-a", { fileId: "page-a", title: "Local", mimeType: "text/plain" });
    mount("user-a");
    await vi.waitFor(() => expect(getRecentFiles).toHaveBeenCalledOnce());
    saveLastDoc("user-a", { fileId: "page-b", title: "Local B", mimeType: "text/plain" });
    flushSync(() =>
      window.dispatchEvent(new StorageEvent("storage", { key: recentDocsStorageKey("user-b") })),
    );
    expect(getRecentFiles).toHaveBeenCalledOnce();
    flushSync(() => window.dispatchEvent(new Event("focus")));
    await vi.waitFor(() => expect(getRecentFiles).toHaveBeenCalledTimes(2));
    flushSync(() => window.dispatchEvent(new Event("focus")));
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
    mount(null);
    flushSync(() =>
      window.dispatchEvent(new StorageEvent("storage", { key: recentDocsStorageKey("user-a") })),
    );
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
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
      expect.objectContaining({ disabled: true, fileId: "form-1" }),
    );
  });

  it("offers a fresh retry after a failed read and cancels it on unmount", async () => {
    saveLastDoc("user-a", { fileId: "file-a", title: "Old title", mimeType: "text/plain" });
    getRecentFiles.mockRejectedValueOnce(new Error("Unavailable"));
    const retry = Promise.withResolvers<[]>();
    getRecentFiles.mockReturnValueOnce(retry.promise);
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages could not be loaded"));
    const section = host.querySelector("section");
    const firstSignal = getRecentFiles.mock.calls[0]?.[1];
    const button = host.querySelector("button");
    expect(button).not.toBeNull();
    if (!button) throw new Error("Retry button missing");
    button.focus();
    expect(document.activeElement).toBe(button);
    flushSync(() => button.click());
    expect(host.querySelector("section")).toBe(section);
    expect(host).toHaveTextContent("Loading recent pages");
    // The first request has already failed. Native queries cancel the live
    // retry when its final observer leaves, without reviving retained errors.
    expect(firstSignal).toBeDefined();
    expect(host.querySelector("button")).toBeNull();
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
    const signal = getRecentFiles.mock.calls[1]?.[1];
    flushSync(() => root.render(null));
    expect(signal?.aborted).toBe(true);
    retry.resolve([]);
    await retry.promise;
    expect(host).toBeEmptyDOMElement();
  });

  it("shows a fresh same-session return immediately without another request", async () => {
    saveLastDoc("user-a", { fileId: "file-a", title: "Local old title", mimeType: "text/plain" });
    getRecentFiles.mockResolvedValue([
      { id: "file-a", name: "Authorized current title", mimeType: "text/plain", driveName: null },
    ]);
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Authorized current title"));
    mount("user-a", false);
    mount("user-a");

    expect(host).toHaveTextContent("Authorized current title");
    expect(host).not.toHaveTextContent("Loading recent pages");
    expect(host).not.toHaveTextContent("Local old title");
    expect(getRecentFiles).toHaveBeenCalledOnce();
  });

  it("rereads the current validated history when the user retries", async () => {
    saveLastDoc("user-a", { fileId: "first", title: "Local first", mimeType: "text/plain" });
    getRecentFiles.mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValueOnce([]);
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages could not be loaded"));
    saveLastDoc("user-a", { fileId: "newer", title: "Local newer", mimeType: "text/plain" });
    const button = host.querySelector("button");
    if (!button) throw new Error("Retry is missing");
    flushSync(() => button.click());
    expect(host).toHaveTextContent("Loading recent pages");
    await vi.waitFor(() => expect(getRecentFiles).toHaveBeenCalledTimes(2));
    expect(getRecentFiles.mock.calls[1]?.[0]).toEqual(["newer", "first"]);
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
  });

  it("hides expired metadata until a new permission response succeeds", async () => {
    saveLastDoc("user-a", { fileId: "file-a", title: "Local", mimeType: "text/plain" });
    getRecentFiles.mockResolvedValueOnce([
      { id: "file-a", name: "Previously authorized", mimeType: "text/plain", driveName: null },
    ]);
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Previously authorized"));
    mount("user-a", false);
    for (const query of client.getQueryCache().findAll({ queryKey: ["wiki", "recent-pages"] })) {
      client.setQueryData(query.queryKey, query.state.data, { updatedAt: Date.now() - 31_000 });
    }
    const pending = Promise.withResolvers<[]>();
    getRecentFiles.mockReturnValueOnce(pending.promise);
    mount("user-a");
    expect(host).toHaveTextContent("Loading recent pages");
    expect(host).not.toHaveTextContent("Previously authorized");
    pending.resolve([]);
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
  });

  it("requires new recent authorization after a known file denial", async () => {
    const page = { id: "file-a", name: "Private page", mimeType: "text/plain", driveName: null };
    saveLastDoc("user-a", { fileId: page.id, title: "Local", mimeType: page.mimeType });
    getRecentFiles.mockResolvedValueOnce([page]).mockResolvedValueOnce([]);
    mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Private page"));
    client.setQueryData(["wiki", "file", page.id], page);
    await expect(
      client.query({
        queryFn: () => {
          throw new ApiError(403, "Permission denied");
        },
        queryKey: ["wiki", "file", page.id],
        staleTime: 0,
      }),
    ).rejects.toThrow("Permission denied");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
    expect(host).not.toHaveTextContent("Private page");
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
  });

  it.each(
    (["active", "prefetch"] as const).flatMap((mode) =>
      ([403, 404, "invalidate", "remove", "success", "mutation"] as const).map((change) => ({
        mode,
        change,
      })),
    ),
  )("fences a pending $mode read after $change authority changes", async ({ mode, change }) => {
    const page = {
      id: "file-a",
      name: "Retired private title",
      mimeType: "text/plain",
      driveName: null,
    };
    const fileKey = ["wiki", "file", page.id];
    client.setQueryData(fileKey, page);
    saveLastDoc("user-a", { fileId: page.id, title: "Local", mimeType: page.mimeType });
    const pending = Promise.withResolvers<(typeof page)[]>();
    getRecentFiles.mockReturnValueOnce(pending.promise).mockResolvedValueOnce([]);
    mount("user-a", mode === "active");
    if (!scope) throw new Error("Recent-page scope missing");
    const previousKey = recentPagesQueryOptions(scope, [page.id]).queryKey;
    const prefetched =
      mode === "prefetch"
        ? client.query(recentPagesQueryOptions(scope, [page.id])).catch(() => null)
        : null;
    await vi.waitFor(() => expect(getRecentFiles).toHaveBeenCalledOnce());
    const signal = getRecentFiles.mock.calls[0]?.[1];
    switch (change) {
      case 403:
      case 404:
        await client
          .query({
            queryFn: () => {
              throw new ApiError(change, "Permission denied");
            },
            queryKey: fileKey,
            staleTime: 0,
          })
          .catch((error: unknown) => {
            if (!(error instanceof ApiError) || error.status !== change) throw error;
          });
        break;
      case "mutation": {
        const result = Promise.withResolvers<boolean>();
        const mutation = new MutationObserver(client, {
          mutationFn: async () => await result.promise,
        });
        const committed = mutation.mutate();
        result.resolve(true);
        await committed;
        break;
      }
      case "invalidate":
        await client.invalidateQueries({ queryKey: fileKey });
        break;
      case "remove":
        client.removeQueries({ queryKey: fileKey });
        break;
      case "success":
        client.setQueryData(fileKey, page);
        break;
    }
    expect(signal?.aborted).toBe(true);
    expect(client.getQueryState(previousKey)).toBeUndefined();
    pending.resolve([page]);
    await pending.promise;
    await prefetched;
    if (mode === "prefetch") mount("user-a");
    await vi.waitFor(() => expect(host).toHaveTextContent("Your recent pages will appear here"));
    expect(host).not.toHaveTextContent(page.name);
    expect(client.getQueryState(previousKey)).toBeUndefined();
    expect(getRecentFiles).toHaveBeenCalledTimes(2);
  });

  it.each(["active", "prefetch"] as const)(
    "allows a pending %s read to finish when authority has not changed",
    async (mode) => {
      const page = {
        id: "file-a",
        name: "Current authorized title",
        mimeType: "text/plain",
        driveName: null,
      };
      saveLastDoc("user-a", { fileId: page.id, title: "Local", mimeType: page.mimeType });
      const pending = Promise.withResolvers<(typeof page)[]>();
      getRecentFiles.mockReturnValueOnce(pending.promise);
      mount("user-a", mode === "active");
      if (!scope) throw new Error("Recent-page scope missing");
      const prefetched =
        mode === "prefetch"
          ? client.query(recentPagesQueryOptions(scope, [page.id])).catch(() => null)
          : null;
      await vi.waitFor(() => expect(getRecentFiles).toHaveBeenCalledOnce());
      const signal = getRecentFiles.mock.calls[0]?.[1];
      pending.resolve([page]);
      await prefetched;
      if (mode === "prefetch") mount("user-a");
      await vi.waitFor(() => expect(host).toHaveTextContent(page.name));
      expect(signal?.aborted).toBe(false);
      expect(getRecentFiles).toHaveBeenCalledOnce();
    },
  );

  it.each([1, 2, 3, 4, 5])(
    "names the recent list honestly for %s authorized pages",
    async (count) => {
      for (let index = 0; index < count; index += 1) {
        saveLastDoc("user-a", { fileId: `file-${index}`, title: "Local", mimeType: "text/plain" });
      }
      getRecentFiles.mockResolvedValue(
        Array.from({ length: count }, (_, index) => ({
          id: `file-${index}`,
          name: `Authorized ${index}`,
          mimeType: "text/plain",
          driveName: null,
        })),
      );
      mount("user-a");
      await vi.waitFor(() => expect(host.querySelectorAll("ul a")).toHaveLength(count));
      expect(host.querySelector("ul")).toHaveAttribute("aria-label", "Recent pages");
    },
  );

  it.each([1, 4, 6])("reserves title-free row geometry for %s validated history IDs", (count) => {
    for (let index = 0; index < count; index += 1) {
      saveLastDoc("user-a", {
        fileId: `file-${index}`,
        title: "Private local label",
        mimeType: "text/plain",
      });
    }
    getRecentFiles.mockReturnValue(Promise.withResolvers<[]>().promise);
    mount("user-a");
    const skeleton = host.querySelector('div[aria-busy="true"] > div[aria-hidden="true"]');
    expect(skeleton?.children).toHaveLength(Math.min(count, 5));
    if (!skeleton) throw new Error("Recent-page skeleton is missing");
    expect([...skeleton.children].every((row) => row.classList.contains("min-h-20"))).toBe(true);
    expect(host.querySelector('[role="status"]')).toHaveTextContent("Loading recent pages");
    expect(host).not.toHaveTextContent("Private local label");
    expect(host.querySelectorAll("a")).toHaveLength(0);
  });
});
