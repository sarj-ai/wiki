import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { WIKI_READ_API } from "@/lib/wiki-read-api";

import { useDriveSearch, useDriveSearchSnippet } from "./use-drive-search";

vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { driveSearch: vi.fn(), driveSearchSnippet: vi.fn() },
}));

function SnippetProbe({ query }: { query: string }) {
  const result = useDriveSearchSnippet("file-1", query, true);
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

function SearchProbe({ enabled }: { enabled: boolean }) {
  const result = useDriveSearch("roadmap", enabled);
  return <span>{result.fetchStatus}</span>;
}

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
    const foreground = client.fetchQuery({
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
