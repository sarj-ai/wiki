// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { BrowserSessionBoundary } from "@/components/browser-session-boundary";
import { recentDocsStorageKey, writeActiveBrowserUser } from "@/lib/browser-storage";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getMe: vi.fn().mockReturnValue(Promise.withResolvers<never>().promise) },
}));

describe("browser session identity boundary", () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    window.sessionStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    container.remove();
    client.clear();
  });

  it("clears the previous user's metadata when immutable bootstrap identity changes", async () => {
    window.localStorage.setItem(recentDocsStorageKey("user-a"), "private-a");
    window.localStorage.setItem(recentDocsStorageKey("user-b"), "private-b");
    writeActiveBrowserUser("user-a");

    flushSync(() => {
      root.render(
        <QueryClientProvider client={client}>
          <BrowserSessionBoundary initialUserId="user-b">
            <IdentityProbe />
          </BrowserSessionBoundary>
        </QueryClientProvider>,
      );
    });

    expect(container.textContent).toBe("user-b");
    await vi.waitFor(() => {
      expect(window.localStorage.getItem(recentDocsStorageKey("user-a"))).toBeNull();
    });
    expect(window.localStorage.getItem(recentDocsStorageKey("user-b"))).toBe("private-b");
  });

  it("does not invent a persistence identity when bootstrap identity is unavailable", () => {
    flushSync(() => {
      root.render(
        <QueryClientProvider client={client}>
          <BrowserSessionBoundary initialUserId={null}>
            <IdentityProbe />
          </BrowserSessionBoundary>
        </QueryClientProvider>,
      );
    });

    expect(container.textContent).toBe("anonymous");
    expect(window.sessionStorage.length).toBe(0);
  });
});

function IdentityProbe() {
  return <span>{useBrowserUserId() ?? "anonymous"}</span>;
}
