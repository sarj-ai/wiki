import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import { wikiKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import type { FileDetail } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

import { DocViewerHost } from "../wiki-document/doc-viewer-host";
import { useDocViewerStore } from "../wiki-document/doc-viewer-store";
import { FilePageClient } from "../wiki-document/file-page-client";
import { useDocumentIntent } from "./use-document-intent";

vi.mock("next/navigation", () => ({ useRouter: () => ({ prefetch: vi.fn() }) }));
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getFile: vi.fn() } }));
vi.mock("@/lib/file-urls", () => ({ embedUrlFor: () => "about:blank" }));
vi.mock("@/components/wiki-document/wiki-toolbar", () => ({ WikiToolbar }));

function WikiToolbar() {
  return <span />;
}

const FILE: FileDetail = {
  breadcrumb: [],
  canEdit: false,
  driveId: undefined,
  driveName: undefined,
  id: "document",
  mimeType: MIME_DOC,
  modifiedTime: undefined,
  name: "Previously authorized",
  parentFolderId: undefined,
};

function IntentLink() {
  const handlers = useDocumentIntent({
    disabled: false,
    fileId: FILE.id,
  });
  return (
    <a {...handlers} href="#document">
      Document
    </a>
  );
}

describe("document intent authorization outcome", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    useDocViewerStore.getState().reset();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    client = new QueryClient({
      defaultOptions: { queries: { gcTime: Infinity, retry: false, staleTime: 60_000 } },
    });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    vi.mocked(WIKI_READ_API.getFile).mockReset();
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    client.clear();
    useDocViewerStore.getState().reset();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  function commit() {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <IntentLink />
        </QueryClientProvider>,
      ),
    );
    const link = host.querySelector("a");
    if (!link) throw new Error("Document link missing");
    flushSync(() => link.click());
  }

  it.each([401, 403, 404, 503])(
    "does not turn a fresh %i into prewarm success using older cache data",
    async (status) => {
      client.setQueryData(wikiKeys.file(FILE.id), FILE, { updatedAt: Date.now() - 120_000 });
      vi.mocked(WIKI_READ_API.getFile).mockRejectedValue(new ApiError(status, "Read denied"));
      commit();
      await vi.advanceTimersByTimeAsync(0);
      expect(WIKI_READ_API.getFile).toHaveBeenCalledOnce();
      expect(client.getQueryData(wikiKeys.file(FILE.id))).toEqual(FILE);
      expect(client.getQueryState(wikiKeys.file(FILE.id))?.status).toBe("error");
      expect(useDocViewerStore.getState().prewarm).toBeNull();
    },
  );

  it("uses metadata returned by the admitted read and allows a later successful retry", async () => {
    client.setQueryData(wikiKeys.file(FILE.id), FILE, { updatedAt: Date.now() - 120_000 });
    vi.mocked(WIKI_READ_API.getFile).mockRejectedValueOnce(new ApiError(403, "Read denied"));
    commit();
    await vi.advanceTimersByTimeAsync(0);
    vi.mocked(WIKI_READ_API.getFile).mockResolvedValue({ ...FILE, name: "Current authorized" });
    commit();
    await vi.advanceTimersByTimeAsync(0);
    expect(WIKI_READ_API.getFile).toHaveBeenCalledTimes(2);
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ title: "Current authorized" });
  });

  it.each([403, 404])(
    "retries a known %i outcome even when the retained metadata timestamp is recent",
    async (status) => {
      client.setQueryData(wikiKeys.file(FILE.id), FILE);
      vi.mocked(WIKI_READ_API.getFile).mockRejectedValue(new ApiError(status, "Read denied"));
      await client
        .query({
          queryFn: async () => await WIKI_READ_API.getFile(FILE.id),
          queryKey: wikiKeys.file(FILE.id),
          staleTime: 0,
        })
        .catch(() => null);
      expect(client.getQueryState(wikiKeys.file(FILE.id))?.dataUpdatedAt).toBe(Date.now());
      commit();
      await vi.advanceTimersByTimeAsync(0);
      expect(WIKI_READ_API.getFile).toHaveBeenCalledTimes(2);
      expect(useDocViewerStore.getState().prewarm).toBeNull();
      vi.mocked(WIKI_READ_API.getFile).mockResolvedValue({ ...FILE, name: "Access restored" });
      commit();
      await vi.advanceTimersByTimeAsync(0);
      expect(WIKI_READ_API.getFile).toHaveBeenCalledTimes(3);
      expect(useDocViewerStore.getState().prewarm?.title).toBe("Access restored");
    },
  );

  it("reuses successful fresh metadata without an additional read", async () => {
    client.setQueryData(wikiKeys.file(FILE.id), FILE);
    commit();
    await vi.advanceTimersByTimeAsync(0);
    expect(WIKI_READ_API.getFile).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm?.fileId).toBe(FILE.id);
  });

  it("keeps explicit query cancellation quiet and prevents its late iframe publication", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(vi.fn());
    const pending = Promise.withResolvers<FileDetail>();
    vi.mocked(WIKI_READ_API.getFile).mockReturnValue(pending.promise);
    commit();
    await vi.advanceTimersByTimeAsync(0);
    const signal = vi.mocked(WIKI_READ_API.getFile).mock.calls[0]?.[1];
    await client.cancelQueries({ exact: true, queryKey: wikiKeys.file(FILE.id) });
    pending.resolve(FILE);
    await vi.advanceTimersByTimeAsync(0);
    expect(signal?.aborted).toBe(true);
    expect(warning).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();
  });

  it("keeps an errored file page inactive and mounts no denied intent iframe", async () => {
    client.setQueryData(wikiKeys.file(FILE.id), FILE);
    vi.mocked(WIKI_READ_API.getFile).mockRejectedValue(new ApiError(403, "Read denied"));
    await client
      .query({
        queryFn: async () => await WIKI_READ_API.getFile(FILE.id),
        queryKey: wikiKeys.file(FILE.id),
        staleTime: 0,
      })
      .catch(() => null);
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <FilePageClient driveId={FILE.id} />
          <IntentLink />
          <DocViewerHost />
        </QueryClientProvider>,
      ),
    );
    const link = host.querySelector("a[href='#document']");
    if (!(link instanceof HTMLAnchorElement)) throw new TypeError("Intent link missing");
    flushSync(() => link.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(host.textContent).toContain("Can't load this file");
    expect(useDocViewerStore.getState().active).toBeNull();
    expect(host.querySelectorAll("iframe")).toHaveLength(0);
  });
});
