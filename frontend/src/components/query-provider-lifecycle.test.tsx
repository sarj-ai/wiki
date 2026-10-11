import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { installLocalStorage } from "@/test-stubs/install-local-storage";

import { BrowserSessionBoundary } from "./browser-session-boundary";
import { QueryProvider } from "./query-provider";

it("purges legacy storage once at the browser session boundary", () => {
  installLocalStorage();
  const legacyKeys = new Set([
    "sarj-wiki-query-cache",
    "sarj-wiki:recent-docs",
    "wiki-collapsed-drives",
    "wiki-expanded-folders",
  ]);
  for (const key of legacyKeys) localStorage.setItem(key, "legacy-metadata");
  const remove = vi.spyOn(localStorage, "removeItem");
  const root = createRoot(document.createElement("div"));
  try {
    flushSync(() =>
      root.render(
        <QueryProvider userId="current-user">
          <BrowserSessionBoundary initialUserId="current-user">
            <span />
          </BrowserSessionBoundary>
        </QueryProvider>,
      ),
    );
    expect(remove.mock.calls.filter(([key]) => legacyKeys.has(key))).toHaveLength(4);
    for (const key of legacyKeys) expect(localStorage.getItem(key)).toBeNull();
  } finally {
    flushSync(() => root.unmount());
    vi.restoreAllMocks();
  }
});
