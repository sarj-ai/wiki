import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { wikiKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import { MIME_DOC, MIME_FOLDER, MIME_HTML, MIME_PDF, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

import { useDriveSearchSnippet, useDriveSearchState } from "./use-drive-search";

vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { driveSearch: vi.fn(), driveSearchSnippet: vi.fn() },
}));

function SnippetProbe({
  fileId = "file-1",
  mimeType,
  query,
}: {
  fileId?: string;
  mimeType?: string;
  query: string;
}) {
  const result = useDriveSearchSnippet(fileId, query, true, mimeType);
  return <span>{result.fetchStatus}</span>;
}

it("skips out-of-contract snippet queries while valid queries still load", async () => {
  const readSnippet = vi.mocked(WIKI_READ_API.driveSearchSnippet).mockResolvedValue({
    fileId: "file-1",
    mimeType: "application/vnd.google-apps.document",
    matchTerm: "ok",
    snippet: "ok",
  });
  const host = document.createElement("div");
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  try {
    flushSync(() => {
      root.render(
        <QueryClientProvider client={client}>
          <SnippetProbe query={"x".repeat(2049)} />
        </QueryClientProvider>,
      );
    });
    expect(readSnippet).not.toHaveBeenCalled();
    expect(host.textContent).toBe("idle");
    flushSync(() => {
      root.render(
        <QueryClientProvider client={client}>
          <SnippetProbe query="ok" />
        </QueryClientProvider>,
      );
    });
    await vi.waitFor(() => {
      expect(readSnippet).toHaveBeenCalledOnce();
    });
  } finally {
    flushSync(() => {
      root.unmount();
    });
    client.clear();
    vi.restoreAllMocks();
  }
});

describe("search preview request budget", () => {
  let root: ReturnType<typeof createRoot>;
  let client: QueryClient;

  function renderSnippet(query: string, mimeType?: string, fileId?: string) {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <SnippetProbe
            query={query}
            {...(mimeType ? { mimeType } : {})}
            {...(fileId ? { fileId } : {})}
          />
        </QueryClientProvider>,
      ),
    );
  }

  beforeEach(() => {
    vi.useFakeTimers();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    root = createRoot(document.createElement("div"));
    vi.mocked(WIKI_READ_API.driveSearchSnippet).mockReset().mockResolvedValue({
      fileId: "file-1",
      mimeType: MIME_DOC,
      matchTerm: "quarterlyreport",
      snippet: "quarterlyreport",
    });
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    client.clear();
    vi.useRealTimers();
  });

  it("loads the initial selection immediately and only the settled query after rapid typing", async () => {
    const query = "quarterlyreport";
    renderSnippet(query.slice(0, 2));
    await vi.advanceTimersByTimeAsync(0);
    expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledOnce();
    for (let length = 3; length <= query.length; length++) {
      window.setTimeout(() => renderSnippet(query.slice(0, length)), (length - 2) * 50);
    }
    await vi.advanceTimersByTimeAsync((query.length - 2) * 50 + 250);
    await vi.waitFor(() => expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledTimes(2));
    expect(vi.mocked(WIKI_READ_API.driveSearchSnippet).mock.calls.map(([, term]) => term)).toEqual([
      "qu",
      query,
    ]);
  });

  it("does not fetch an old query for a newly selected file while typing is unsettled", async () => {
    renderSnippet("road", MIME_DOC);
    await vi.advanceTimersByTimeAsync(0);
    renderSnippet("roadmap", MIME_DOC, "file-2");
    await vi.advanceTimersByTimeAsync(100);
    expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(150);
    await vi.waitFor(() =>
      expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenLastCalledWith(
        "file-2",
        "roadmap",
        MIME_DOC,
        expect.any(AbortSignal),
      ),
    );
  });

  it("keeps live invalid queries disabled even while a valid debounced value remains", async () => {
    renderSnippet("road");
    await vi.advanceTimersByTimeAsync(0);
    renderSnippet("x".repeat(2049), MIME_DOC, "file-2");
    await vi.advanceTimersByTimeAsync(300);
    expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledOnce();
  });

  it.each([MIME_FOLDER, MIME_PDF, MIME_HTML, "application/vnd.google-apps.form"])(
    "skips preview reads for known non-exportable MIME %s",
    async (mimeType) => {
      renderSnippet("roadmap", mimeType);
      await vi.advanceTimersByTimeAsync(300);
      expect(WIKI_READ_API.driveSearchSnippet).not.toHaveBeenCalled();
    },
  );

  it.each([MIME_DOC, MIME_SHEET, MIME_SLIDE, undefined])(
    "retains preview reads for supported or unknown MIME %s",
    async (mimeType) => {
      renderSnippet("roadmap", mimeType);
      await vi.advanceTimersByTimeAsync(0);
      expect(WIKI_READ_API.driveSearchSnippet).toHaveBeenCalledOnce();
    },
  );
});

function CachedSearchProbe({ query }: { query: string }) {
  useDriveSearchState(query);
  return null;
}

it("collects abandoned search terms after ten minutes while preserving an observed fresh result", async () => {
  vi.useFakeTimers();
  const read = vi.mocked(WIKI_READ_API.driveSearch).mockReset().mockResolvedValue([]);
  const client = new QueryClient({
    defaultOptions: { queries: { gcTime: 24 * 60 * 60 * 1000, retry: false } },
  });
  const root = createRoot(document.createElement("div"));
  function render(query: null | string) {
    flushSync(() =>
      root.render(
        query === null ? null : (
          <QueryClientProvider client={client}>
            <CachedSearchProbe key={query} query={query} />
          </QueryClientProvider>
        ),
      ),
    );
  }
  try {
    await Array.from({ length: 100 }, (_, index) => index).reduce(async (previous, index) => {
      await previous;
      render(`term-${index}`);
      await vi.advanceTimersByTimeAsync(0);
      render(null);
    }, Promise.resolve());
    expect(client.getQueryCache().getAll()).toHaveLength(100);
    render("term-99");
    await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(100);
    expect(client.getQueryData(wikiKeys.driveSearch("term-99"))).toEqual([]);
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 1);
    expect(client.getQueryCache().getAll()).toHaveLength(1);
    expect(client.getQueryData(wikiKeys.driveSearch("term-99"))).toEqual([]);
    expect(read).toHaveBeenCalledTimes(100);
  } finally {
    flushSync(() => root.unmount());
    client.clear();
    vi.useRealTimers();
  }
});

function SearchProbe({ enabled }: { enabled: boolean }) {
  const result = useDriveSearchState("roadmap", enabled).query;
  return <span>{result.fetchStatus}</span>;
}

it.each([
  { expectedCalls: 1, expectedQuery: "roadmap", label: "too short", nextQuery: "x" },
  { expectedCalls: 1, expectedQuery: "roadmap", label: "too long", nextQuery: "x".repeat(2049) },
  {
    expectedCalls: 2,
    expectedQuery: "new roadmap",
    label: "replacement",
    nextQuery: "new roadmap",
  },
])(
  "does not refetch the discarded main-search term while the $label input is unsettled",
  async ({ expectedCalls, expectedQuery, nextQuery }) => {
    vi.useFakeTimers();
    const read = vi.mocked(WIKI_READ_API.driveSearch).mockReset().mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const root = createRoot(document.createElement("div"));
    function render(query: string) {
      flushSync(() =>
        root.render(
          <QueryClientProvider client={client}>
            <CachedSearchProbe query={query} />
          </QueryClientProvider>,
        ),
      );
    }
    try {
      render("roadmap");
      await vi.advanceTimersByTimeAsync(0);
      expect(read).toHaveBeenCalledOnce();
      render(nextQuery);
      await client.invalidateQueries({ queryKey: wikiKeys.driveSearch("roadmap") });
      expect(read).toHaveBeenCalledOnce();
      await vi.advanceTimersByTimeAsync(249);
      expect(read).toHaveBeenCalledOnce();
      await vi.advanceTimersByTimeAsync(1);
      await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(expectedCalls));
      expect(read).toHaveBeenLastCalledWith(expectedQuery, 10, expect.any(AbortSignal));
    } finally {
      flushSync(() => root.unmount());
      client.clear();
      vi.useRealTimers();
    }
  },
);

it("disables closed-palette reads without cancelling a shared foreground consumer", async () => {
  const pending = Promise.withResolvers<[]>();
  const search = vi.mocked(WIKI_READ_API.driveSearch).mockReset().mockReturnValue(pending.promise);
  const host = document.createElement("div");
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const render = (enabled: boolean) =>
    flushSync(() => {
      root.render(
        <QueryClientProvider client={client}>
          <SearchProbe enabled={enabled} />
        </QueryClientProvider>,
      );
    });
  try {
    render(false);
    await client.invalidateQueries({ queryKey: ["wiki", "drive-search", "roadmap"] });
    expect(search).not.toHaveBeenCalled();
    render(true);
    await vi.waitFor(() => expect(search).toHaveBeenCalledOnce());
    const signal = search.mock.calls[0]?.[2];
    const foreground = client.query({
      queryFn: async () => await WIKI_READ_API.driveSearch("roadmap", 10),
      queryKey: ["wiki", "drive-search", "roadmap"],
    });
    render(false);
    expect(signal?.aborted).toBe(false);
    pending.resolve([]);
    await expect(foreground).resolves.toEqual([]);
    await client.invalidateQueries({ queryKey: ["wiki", "drive-search", "roadmap"] });
    expect(search).toHaveBeenCalledOnce();
  } finally {
    flushSync(() => root.unmount());
    client.clear();
    vi.restoreAllMocks();
  }
});
