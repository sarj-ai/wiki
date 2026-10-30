import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { SidebarProvider } from "@/components/ui/sidebar";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DocIcon, DriveNode, DriveSummary, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_FORM, MIME_SHORTCUT } from "@/types/wiki";

import { FileRow } from "./file-row";
import { useDriveTreeIndex } from "./use-drive-tree-index";
import { WikiSidebar } from "./wiki-sidebar";

const PATHNAME = vi.hoisted(() => vi.fn(() => "/"));
const ICON_READ = vi.hoisted(() => vi.fn<() => Promise<DocIcon[]>>());
const DRIVES_READ = vi.hoisted(() => vi.fn<() => Promise<DriveSummary[]>>());
vi.mock("next/navigation", () => ({
  usePathname: PATHNAME,
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getDrives: DRIVES_READ, listDocIcons: ICON_READ },
}));
vi.mock("./drive-section", () => ({
  DriveSection: vi.fn(function DriveSection({
    docIcons,
    drive,
    folderIcons,
    sortOrders,
  }: {
    docIcons: readonly DocIcon[];
    drive: { id: string };
    folderIcons: readonly FolderIcon[];
    sortOrders: readonly SortOrder[];
  }) {
    const index = useDriveTreeIndex(drive.id, docIcons, folderIcons, sortOrders, false);
    return <span data-drive-id={drive.id}>{index.docIconOverrides.size}</span>;
  }),
}));
vi.mock("./drive-search-box", () => ({ DriveSearchBox: vi.fn(() => null) }));
vi.mock("./wiki-footer", () => ({ WikiFooter: vi.fn(() => null) }));
vi.mock("./sidebar-resize-handle", () => ({ SidebarResizeHandle: vi.fn(() => null) }));
vi.mock("./use-wiki-reorder", () => ({
  useWikiReorder: () => ({ activeNode: null, optimisticByContainer: new Map(), sensors: [] }),
}));
vi.mock("./use-document-intent", () => ({ useDocumentIntent: () => ({}) }));
vi.mock("./item-menu", () => ({ ItemMenu: vi.fn(() => null) }));

describe("sidebar subscriptions", () => {
  let queryClient: QueryClient;
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    ICON_READ.mockResolvedValue([]);
    DRIVES_READ.mockResolvedValue([]);
    PATHNAME.mockReturnValue("/");
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const drives = Array.from({ length: 20 }, (_, index) => ({
      id: `drive-${index}`,
      isPersonal: false,
      name: `Workspace ${index}`,
    }));
    queryClient.setQueryData(wikiKeys.drives, drives);
    queryClient.setQueryData(wikiKeys.preferences, {
      showNonDocFiles: true,
      hiddenWorkspaceIds: [],
    });
    queryClient.setQueryData(wikiKeys.folderIcons, []);
    queryClient.setQueryData(wikiKeys.docIcons, []);
    queryClient.setQueryData(wikiKeys.sortOrders, []);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    queryClient.clear();
    vi.restoreAllMocks();
  });

  it("shares one document-icon observer among 20 drive indexes", () => {
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    expect(
      queryClient.getQueryCache().find({ queryKey: wikiKeys.docIcons })?.getObserversCount(),
    ).toBe(1);
    expect(ICON_READ).not.toHaveBeenCalled();
  });

  it("provides a focusable logo link back to recent pages from a document route", () => {
    PATHNAME.mockReturnValue("/d/doc");
    queryClient.setQueryDefaults(wikiKeys.file("doc"), { staleTime: Infinity });
    queryClient.setQueryData(wikiKeys.file("doc"), { breadcrumb: [] });
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    const home = host.querySelector('a[aria-label="Recent pages"]');
    expect(home).toBeInstanceOf(HTMLAnchorElement);
    expect(home).toHaveAttribute("href", "/");
    expect(home).toHaveClass("focus-visible:ring-2");
    expect(home?.querySelector('svg[role="img"]')).toHaveAttribute("aria-label", "Sarj");
  });

  it("does not request document icons when all drives are hidden", () => {
    queryClient.removeQueries({ queryKey: wikiKeys.docIcons });
    queryClient.setQueryData(wikiKeys.preferences, {
      showNonDocFiles: true,
      hiddenWorkspaceIds: Array.from({ length: 20 }, (_, index) => `drive-${index}`),
    });
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    expect(ICON_READ).not.toHaveBeenCalled();
  });

  it("exposes cold drive loading without claiming an empty successful result", () => {
    queryClient.removeQueries({ queryKey: wikiKeys.drives });
    DRIVES_READ.mockReturnValue(Promise.withResolvers<DriveSummary[]>().promise);
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    expect(host.querySelector('[role="status"]')).toHaveTextContent("Loading drives");
    expect(host.querySelector('[role="status"] [aria-hidden="true"]')?.children).toHaveLength(3);
    expect(host).not.toHaveTextContent("No accessible drives");
  });

  it("offers a fresh drive retry and shows empty feedback only after success", async () => {
    queryClient.removeQueries({ queryKey: wikiKeys.drives });
    DRIVES_READ.mockRejectedValueOnce(new Error("Unavailable"));
    const retry = Promise.withResolvers<DriveSummary[]>();
    DRIVES_READ.mockReturnValueOnce(retry.promise);
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    await vi.waitFor(() =>
      expect(host.querySelector('[role="alert"]')).toHaveTextContent("Failed to load drives"),
    );
    expect(host).not.toHaveTextContent("No accessible drives");
    const button = [...host.querySelectorAll("button")].find(
      (candidate) => candidate.textContent === "Retry drives",
    );
    if (!button) throw new Error("Drive retry is missing");
    flushSync(() => button.click());
    await vi.waitFor(() => expect(DRIVES_READ).toHaveBeenCalledTimes(2));
    expect(host).not.toHaveTextContent("No accessible drives");
    retry.resolve([]);
    await vi.waitFor(() => expect(host).toHaveTextContent("No accessible drives"));
    expect(host).not.toHaveTextContent("Failed to load drives");
  });

  it("keeps cached visible drive rows during a foreground refetch", async () => {
    const retry = Promise.withResolvers<DriveSummary[]>();
    DRIVES_READ.mockReturnValueOnce(retry.promise);
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    expect(host.querySelectorAll("[data-drive-id]")).toHaveLength(20);
    const refresh = queryClient.refetchQueries({ queryKey: wikiKeys.drives });
    await vi.waitFor(() => expect(DRIVES_READ).toHaveBeenCalledOnce());
    expect(host.querySelectorAll("[data-drive-id]")).toHaveLength(20);
    retry.resolve([]);
    await refresh;
    await vi.waitFor(() => expect(host).toHaveTextContent("No accessible drives"));
  });

  it("renders 500 normal rows without repeating the sidebar pathname subscription", () => {
    const view = renderToStaticMarkup(
      wrap(
        <>
          {Array.from({ length: 500 }, (_, index) => (
            <FileRow
              activeDriveId="file-499"
              depth={0}
              driveId="drive-1"
              iconOverride={undefined}
              key={index}
              node={node(`file-${index}`)}
              parentFolderId={undefined}
              pathname="/d/file-499"
            />
          ))}
        </>,
      ),
    );
    expect(PATHNAME).not.toHaveBeenCalled();
    expect(view.match(/data-active="true"/g)).toHaveLength(1);
  });

  it("retains direct-row encoded pathname selection without a parsed active ID", () => {
    PATHNAME.mockReturnValue("/d/file%20id");
    const view = renderToStaticMarkup(
      wrap(
        <FileRow
          activeDriveId={null}
          depth={0}
          driveId={undefined}
          iconOverride={undefined}
          node={node("file id")}
          parentFolderId={undefined}
        />,
      ),
    );
    expect(PATHNAME).toHaveBeenCalledOnce();
    expect(view).toContain('data-active="true"');
    expect(view).toContain('href="/d/file%20id"');
  });

  it("retains active shortcut targets and external form navigation", () => {
    const shortcut = {
      ...node("shortcut"),
      mimeType: MIME_SHORTCUT,
      shortcutTargetId: "file id",
      shortcutTargetMimeType: MIME_DOC,
    };
    const view = renderToStaticMarkup(
      wrap(
        <>
          <FileRow
            activeDriveId="file id"
            depth={0}
            driveId={undefined}
            iconOverride={undefined}
            node={shortcut}
            parentFolderId={undefined}
            pathname="/d/file%20id"
          />
          <FileRow
            activeDriveId="form"
            depth={0}
            driveId={undefined}
            iconOverride={undefined}
            node={{ ...node("form"), mimeType: MIME_FORM }}
            parentFolderId={undefined}
            pathname="/d/form"
          />
        </>,
      ),
    );
    expect(view.match(/data-active="true"/g)).toHaveLength(1);
    expect(view).toContain('href="/d/file%20id"');
    expect(view).toContain('href="https://docs.google.com/forms/d/form/edit"');
  });

  it("formats edited timestamps only for rows that display that timestamp in their title", () => {
    const format = vi.spyOn(Date.prototype, "valueOf");
    const view = renderToStaticMarkup(
      wrap(
        <>
          {Array.from({ length: 500 }, (_, index) => (
            <FileRow
              activeDriveId={null}
              depth={0}
              driveId={undefined}
              iconOverride={undefined}
              key={index}
              node={{
                ...node(`form-${index}`),
                mimeType: MIME_FORM,
                modifiedTime: "2026-10-10T12:00:00Z",
              }}
              parentFolderId={undefined}
              pathname="/"
            />
          ))}
          <FileRow
            activeDriveId={null}
            depth={0}
            driveId={undefined}
            iconOverride={undefined}
            node={{ ...node("doc"), modifiedTime: "2026-10-10T12:00:00Z" }}
            parentFolderId={undefined}
            pathname="/"
          />
        </>,
      ),
    );
    expect(format).toHaveBeenCalledOnce();
    expect(view).toContain("form-0 — opens in a new tab");
    expect(view).toContain("doc — edited Oct 10, 2026");
  });

  function wrap(children: ReactNode) {
    return (
      <QueryClientProvider client={queryClient}>
        <SidebarProvider>{children}</SidebarProvider>
      </QueryClientProvider>
    );
  }
});

function node(id: string): DriveNode {
  return {
    id,
    name: id,
    mimeType: MIME_DOC,
    parentId: undefined,
    modifiedTime: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
