import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { QuickLinkVisitRecorder } from "@/components/quick-links/quick-link-visit-recorder";
import { recentAliasesStorageKey } from "@/lib/browser-storage";
import { readRecentAliases } from "@/lib/recent-aliases";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: { id: "user-1" } }),
}));

describe("QuickLinkVisitRecorder", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/d/file-id?go=team-handbook&view=outline");
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    host.remove();
    vi.restoreAllMocks();
  });

  it("records the alias for the current user and removes the handoff query", async () => {
    flushSync(() => {
      root.render(<QuickLinkVisitRecorder alias="team-handbook" />);
    });
    await vi.waitFor(() => {
      expect(readRecentAliases("user-1")).toHaveLength(1);
    });

    expect(window.location.pathname + window.location.search).toBe("/d/file-id?view=outline");
  });

  it("writes a visit once across Strict Mode replay and profile-object updates", () => {
    const write = vi.spyOn(localStorage, "setItem");
    renderVisit(root);
    renderVisit(root);
    expect(
      write.mock.calls.filter(([key]) => key === recentAliasesStorageKey("user-1")),
    ).toHaveLength(1);
    expect(readRecentAliases("user-1")).toHaveLength(1);
  });
});

function renderVisit(root: Root) {
  flushSync(() => {
    root.render(
      <StrictMode>
        <QuickLinkVisitRecorder alias="team-handbook" />
      </StrictMode>,
    );
  });
}
