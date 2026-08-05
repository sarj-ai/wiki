import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { QuickLinksLoader } from "@/components/quick-links/quick-links-loader";
import { readRecentAliases, writeRecentAliases } from "@/lib/recent-aliases";
import { installLocalStorage } from "@/test-stubs/local-storage";

const getRecentDocumentAliasesAction = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: { id: "user-1" }, isError: false }),
}));

vi.mock("@/app/actions/wiki-actions", () => ({ getRecentDocumentAliasesAction }));

describe("QuickLinksLoader", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    writeRecentAliases("user-1", [
      { alias: "revoked-page", visitedAt: "2026-08-04T12:00:00Z" },
      { alias: "team-handbook", visitedAt: "2026-08-03T12:00:00Z" },
    ]);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    getRecentDocumentAliasesAction.mockReset();
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    host.remove();
  });

  it("renders only revalidated aliases and purges revoked local entries", async () => {
    getRecentDocumentAliasesAction.mockResolvedValue({
      data: [
        {
          alias: "team-handbook",
          driveName: "People",
          mimeType: "application/vnd.google-apps.document",
          name: "Team handbook",
        },
      ],
      ok: true,
    });

    flushSync(() => {
      root.render(<QuickLinksLoader />);
    });
    expect(host).not.toHaveTextContent("revoked-page");
    await vi.waitFor(() => {
      expect(host).toHaveTextContent("Team handbook");
    });

    expect(host).not.toHaveTextContent("revoked-page");
    expect(readRecentAliases("user-1").map((visit) => visit.alias)).toEqual(["team-handbook"]);
  });

  it("fails closed without rendering cached aliases", async () => {
    getRecentDocumentAliasesAction.mockResolvedValue({
      error: "unavailable",
      ok: false,
      status: 502,
    });

    flushSync(() => {
      root.render(<QuickLinksLoader />);
    });
    await vi.waitFor(() => {
      expect(host).toHaveTextContent("Unable to load quick links");
    });

    expect(host).not.toHaveTextContent("revoked-page");
    expect(host).not.toHaveTextContent("team-handbook");
  });
});
