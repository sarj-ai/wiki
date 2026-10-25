import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { readRecentDocs } from "@/lib/last-doc";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import type { FileDetail } from "@/types/wiki";
import { MIME_DOC, MIME_HTML } from "@/types/wiki";

import { useDocViewerStore } from "./doc-viewer-store";
import { DocWarmup } from "./doc-warmup";

vi.mock("@/lib/last-doc", () => ({
  readRecentDocs: vi.fn(),
}));
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getFile: vi.fn() } }));

const FILE: FileDetail = {
  breadcrumb: [],
  canEdit: false,
  driveId: undefined,
  driveName: undefined,
  id: "recent",
  mimeType: MIME_DOC,
  modifiedTime: undefined,
  name: "Authorized recent",
  parentFolderId: undefined,
};

describe("idle document warmup ownership", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;
  let pending: ReturnType<typeof Promise.withResolvers<FileDetail>>;

  beforeEach(() => {
    vi.useFakeTimers();
    useDocViewerStore.getState().reset();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    host = document.createElement("div");
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
    pending = Promise.withResolvers<FileDetail>();
    vi.mocked(readRecentDocs)
      .mockReset()
      .mockReturnValue([{ fileId: "recent", mimeType: MIME_DOC, title: "Recent" }]);
    vi.mocked(WIKI_READ_API.getFile).mockReset().mockReturnValue(pending.promise);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    client.clear();
    useDocViewerStore.getState().reset();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("publishes only successfully authorized idle documents", async () => {
    renderWarmup(root, client);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(WIKI_READ_API.getFile).toHaveBeenCalledOnce();
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(useDocViewerStore.getState().warm).toEqual([
      { fileId: "recent", mimeType: MIME_DOC, reloadKey: 0, title: "Authorized recent" },
    ]);
  });

  it.each([403, 404])(
    "does not warm retained recent metadata after a known %i outcome",
    async (status) => {
      client.setQueryData(["wiki", "file", "recent"], FILE);
      vi.mocked(WIKI_READ_API.getFile).mockRejectedValue(new ApiError(status, "Read denied"));
      await client
        .query({
          queryKey: ["wiki", "file", "recent"],
          queryFn: async () => await WIKI_READ_API.getFile("recent"),
          staleTime: 0,
        })
        .catch(() => null);
      client.setDefaultOptions({ queries: { gcTime: Infinity, retry: false, staleTime: 60_000 } });
      renderWarmup(root, client);
      await vi.advanceTimersByTimeAsync(3_000);
      expect(WIKI_READ_API.getFile).toHaveBeenCalledTimes(2);
      expect(useDocViewerStore.getState().warm).toEqual([]);
      expect(client.getQueryData(["wiki", "file", "recent"])).toEqual(FILE);
      flushSync(() => root.render(null));
      vi.mocked(WIKI_READ_API.getFile).mockResolvedValue({ ...FILE, name: "Access restored" });
      renderWarmup(root, client);
      await vi.advanceTimersByTimeAsync(3_000);
      expect(WIKI_READ_API.getFile).toHaveBeenCalledTimes(3);
      expect(useDocViewerStore.getState().warm[0]?.title).toBe("Access restored");
    },
  );

  it("does not authorize idle HTML recents that the viewer cannot mount in the background", async () => {
    vi.mocked(readRecentDocs).mockReturnValue([
      { fileId: "html-recent", mimeType: MIME_HTML, title: "HTML recent" },
    ]);
    renderWarmup(root, client);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().warm).toEqual([]);
  });

  it("uses the next eligible recent document without increasing the one-document warm limit", async () => {
    vi.mocked(readRecentDocs).mockReturnValue([
      { fileId: "html-recent", mimeType: MIME_HTML, title: "HTML recent" },
      { fileId: "recent", mimeType: MIME_DOC, title: "Recent" },
      { fileId: "later-recent", mimeType: MIME_DOC, title: "Later recent" },
    ]);
    renderWarmup(root, client);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(WIKI_READ_API.getFile).toHaveBeenCalledExactlyOnceWith(
      "recent",
      expect.any(AbortSignal),
    );
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(useDocViewerStore.getState().warm[0]?.fileId).toBe("recent");
  });

  it("does not publish a late authorization after navigation, even after leaving that document", async () => {
    renderWarmup(root, client);
    await vi.advanceTimersByTimeAsync(3_000);
    const owner = Symbol("foreground");
    useDocViewerStore
      .getState()
      .setActive(
        { fileId: "foreground", mimeType: MIME_DOC, reloadKey: 0, title: "Foreground" },
        owner,
      );
    useDocViewerStore.getState().clearActive(owner);
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(useDocViewerStore.getState().warm).toEqual([]);
  });

  it("leaves a shared foreground authorization running when its idle owner unmounts", async () => {
    renderWarmup(root, client);
    await vi.advanceTimersByTimeAsync(3_000);
    const signal = vi.mocked(WIKI_READ_API.getFile).mock.calls[0]?.[1];
    flushSync(() => root.render(null));
    expect(signal?.aborted).toBe(false);
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(useDocViewerStore.getState().warm).toEqual([]);
    expect(client.getQueryData(["wiki", "file", "recent"])).toBeDefined();
  });

  it("skips iframe publication if the tab becomes hidden while authorization is pending", async () => {
    renderWarmup(root, client);
    await vi.advanceTimersByTimeAsync(3_000);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(useDocViewerStore.getState().warm).toEqual([]);
  });

  it("skips idle provider work without a verified user", async () => {
    renderWarmup(root, client, null);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
  });

  it("discards a previous user's pending publication and warms for the replacement user", async () => {
    renderWarmup(root, client, "user-a");
    await vi.advanceTimersByTimeAsync(3_000);
    const previousClient = client;
    client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
    vi.mocked(WIKI_READ_API.getFile).mockResolvedValue({ ...FILE, name: "User B document" });
    renderWarmup(root, client, "user-b");
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(useDocViewerStore.getState().warm).toEqual([]);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(useDocViewerStore.getState().warm[0]?.title).toBe("User B document");
    expect(WIKI_READ_API.getFile).toHaveBeenCalledTimes(2);
    previousClient.clear();
  });
});

function renderWarmup(root: Root, client: QueryClient, userId: null | string = "user-a") {
  flushSync(() => {
    root.render(
      <QueryClientProvider client={client}>
        <BrowserUserContext value={userId}>
          <DocWarmup />
        </BrowserUserContext>
      </QueryClientProvider>,
    );
  });
}
