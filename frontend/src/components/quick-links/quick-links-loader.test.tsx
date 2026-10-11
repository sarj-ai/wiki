import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { QuickLinksLoader } from "@/components/quick-links/quick-links-loader";
import { recentAliasesStorageKey } from "@/lib/browser-storage";
import { readRecentAliases, writeRecentAliases } from "@/lib/recent-aliases";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

const getRecentDocumentAliasesAction = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: { id: "user-1" }, isError: false }),
}));

vi.mock("@/app/actions/wiki-actions", () => ({ getRecentDocumentAliasesAction }));
vi.mock("@/components/wiki-sidebar/use-document-intent", () => ({
  useDocumentIntent: () => ({}),
}));

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
    vi.restoreAllMocks();
  });

  it("preserves new visits and refreshed timestamps recorded while authorization is pending", async () => {
    const pending = Promise.withResolvers<{
      data: { alias: string; driveName: string; fileId: string; mimeType: string; name: string }[];
      ok: true;
    }>();
    getRecentDocumentAliasesAction.mockReturnValue(pending.promise);
    flushSync(() => root.render(<QuickLinksLoader />));
    expect(getRecentDocumentAliasesAction).toHaveBeenCalledOnce();
    writeRecentAliases("user-1", [
      { alias: "new-page", visitedAt: "2026-10-11T12:00:00Z" },
      { alias: "revoked-page", visitedAt: "2026-10-11T11:00:00Z" },
      { alias: "team-handbook", visitedAt: "2026-08-03T12:00:00Z" },
    ]);
    pending.resolve({
      data: [
        {
          alias: "team-handbook",
          driveName: "People",
          fileId: "file-team",
          mimeType: "application/vnd.google-apps.document",
          name: "Team handbook",
        },
      ],
      ok: true,
    });
    await vi.waitFor(() => expect(host).toHaveTextContent("Team handbook"));
    expect(readRecentAliases("user-1").map((visit) => visit.alias)).toEqual([
      "new-page",
      "revoked-page",
      "team-handbook",
    ]);
    expect(host).not.toHaveTextContent("new-page");
    expect(host).not.toHaveTextContent("revoked-page");
  });

  it("does not rewrite recent visits when authorization has nothing to prune", async () => {
    const visits = readRecentAliases("user-1");
    getRecentDocumentAliasesAction.mockResolvedValue({
      data: visits.map((visit) => ({
        alias: visit.alias,
        driveName: "People",
        fileId: `file-${visit.alias}`,
        mimeType: "application/vnd.google-apps.document",
        name: visit.alias,
      })),
      ok: true,
    });
    const writes = vi.spyOn(localStorage, "setItem");
    flushSync(() => root.render(<QuickLinksLoader />));
    await vi.waitFor(() => expect(host).toHaveTextContent("team-handbook"));
    expect(
      writes.mock.calls.filter(([key]) => key === recentAliasesStorageKey("user-1")),
    ).toHaveLength(0);
    expect(readRecentAliases("user-1")).toEqual(visits);
  });

  it("renders only revalidated aliases and purges revoked local entries", async () => {
    getRecentDocumentAliasesAction.mockResolvedValue({
      data: [
        {
          alias: "team-handbook",
          driveName: "People",
          fileId: "file-team",
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
