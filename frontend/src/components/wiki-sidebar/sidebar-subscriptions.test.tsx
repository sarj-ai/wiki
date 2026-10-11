import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { SidebarProvider } from "@/components/ui/sidebar";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_FORM, MIME_SHORTCUT } from "@/types/wiki";

import { FileRow } from "./file-row";
import { useDriveTreeIndex } from "./use-drive-tree-index";
import { WikiSidebar } from "./wiki-sidebar";

const PATHNAME = vi.hoisted(() => vi.fn(() => "/"));
const ICON_READ = vi.hoisted(() => vi.fn<() => Promise<DocIcon[]>>());
vi.mock("next/navigation", () => ({
  usePathname: PATHNAME,
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { listDocIcons: ICON_READ } }));
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
    return <span>{index.docIconOverrides.size}</span>;
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

  it("does not request document icons when all drives are hidden", () => {
    queryClient.removeQueries({ queryKey: wikiKeys.docIcons });
    queryClient.setQueryData(wikiKeys.preferences, {
      showNonDocFiles: true,
      hiddenWorkspaceIds: Array.from({ length: 20 }, (_, index) => `drive-${index}`),
    });
    flushSync(() => root.render(wrap(<WikiSidebar />)));
    expect(ICON_READ).not.toHaveBeenCalled();
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
