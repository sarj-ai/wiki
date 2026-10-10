import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import {
  driveTreeQueryOptions,
  useDocIcons,
  useDrives,
  useFolderIcons,
  usePreferences,
  useSortOrders,
} from "@/hooks/use-wiki-tree";
import type { WIKI_READ_API } from "@/lib/wiki-read-api";

type WikiReadApi = typeof WIKI_READ_API;

const API = vi.hoisted(() => ({
  getDrives: vi.fn<WikiReadApi["getDrives"]>(),
  getPreferences: vi.fn<WikiReadApi["getPreferences"]>(),
  listDocIcons: vi.fn<WikiReadApi["listDocIcons"]>(),
  listFolderIcons: vi.fn<WikiReadApi["listFolderIcons"]>(),
  listSortOrders: vi.fn<WikiReadApi["listSortOrders"]>(),
}));

vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: API }));

describe("Drive tree query gating", () => {
  it("disables the query while its Drive is collapsed", () => {
    expect(driveTreeQueryOptions("drive-1", false).enabled).toBe(false);
    expect(driveTreeQueryOptions("drive-1", true).enabled).toBe(true);
    expect(driveTreeQueryOptions(null, true).enabled).toBe(false);
  });
});

describe("sidebar resource cancellation", () => {
  it("passes abort signals to every global sidebar read and aborts them after unmount", () => {
    const methods = [
      API.getDrives,
      API.getPreferences,
      API.listDocIcons,
      API.listFolderIcons,
      API.listSortOrders,
    ];
    for (const method of methods) method.mockImplementation(pendingUntilAborted);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
      flushSync(() => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <SidebarQueries />
          </QueryClientProvider>,
        );
      });
      const signals = methods.map((method) => method.mock.calls[0]?.[0]);
      expect(signals).toHaveLength(5);
      for (const signal of signals) {
        expect(signal).toBeInstanceOf(AbortSignal);
        expect(signal?.aborted).toBe(false);
      }
      flushSync(() => root.unmount());
      for (const signal of signals) expect(signal?.aborted).toBe(true);
    } finally {
      queryClient.clear();
      host.remove();
    }
  });
});

function SidebarQueries() {
  useDrives();
  usePreferences();
  useDocIcons();
  useFolderIcons();
  useSortOrders();
  return <span hidden />;
}

async function pendingUntilAborted(signal?: AbortSignal): Promise<never> {
  if (!signal) throw new Error("Query cancellation signal is missing");
  return await new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("Query aborted")), { once: true });
  });
}
