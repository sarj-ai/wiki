import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { SidebarProvider } from "@/components/ui/sidebar";
import { resetExpansionState } from "@/hooks/use-expanded-folders";
import { folderFilesQueryOptions, useFolderFiles } from "@/hooks/use-wiki-tree";
import { injectNewFileIntoCaches, optimisticMoveFile } from "@/lib/wiki-cache";
import { EMPTY_DOC_ICONS, EMPTY_FOLDER_ICONS, EMPTY_SORT_ORDERS } from "@/lib/wiki-indexes";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveTree } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { DriveSectionProvider, type DriveSectionValue } from "./drive-context";
import { TreeNode } from "./tree-node";
import { useDriveTreeIndex } from "./use-drive-tree-index";

const READ = vi.hoisted(() =>
  vi.fn<(folderId: string, signal: AbortSignal) => Promise<DriveNode[]>>(),
);
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getFolderFiles: READ } }));
vi.mock("./item-menu", () => ({ ItemMenu: vi.fn(() => null) }));
vi.mock("./create-file-menu", () => ({ CreateFileMenu: vi.fn(() => null) }));
vi.mock("./pickable-icon", () => ({ PickableIcon: vi.fn(() => null) }));
vi.mock("./file-row", () => ({
  FileRow: vi.fn(({ node: rowNode }: { node: DriveNode }) => <span>{rowNode.name}</span>),
}));

describe("folder listing observer ownership", () => {
  let queryClient: QueryClient;
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    READ.mockReset().mockResolvedValue([]);
    resetExpansionState(null);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    queryClient.clear();
    resetExpansionState(null);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  function wrap(
    children: ReactNode,
    complete = true,
    childrenByParent = new Map<string, DriveNode[]>(),
  ) {
    const value: DriveSectionValue = {
      activeDriveId: null,
      childrenByParent,
      docIconOverrides: new Map(),
      driveId: "drive",
      filesByParent: new Map([["folder-0", [node("doc", MIME_DOC)]]]),
      iconOverrides: new Map(),
      optimisticByContainer: new Map(),
      pathname: "/",
      showNonDocFiles: true,
      sortOrderMap: new Map(),
      treeFilesComplete: complete,
    };
    return (
      <QueryClientProvider client={queryClient}>
        <SidebarProvider>
          <DriveSectionProvider value={value}>{children}</DriveSectionProvider>
        </SidebarProvider>
      </QueryClientProvider>
    );
  }

  function toggle() {
    const button = host.querySelector('[data-file-id="folder-0"]');
    if (!(button instanceof HTMLButtonElement)) throw new TypeError("Folder trigger missing");
    flushSync(() => button.click());
  }

  it(
    "keeps 500 complete collapsed folders free of lazy-list observers while retaining counts",
    { timeout: 30_000 },
    () => {
      flushSync(() =>
        root.render(
          wrap(
            <>
              {Array.from({ length: 500 }, (_, index) => (
                <TreeNode
                  depth={0}
                  key={index}
                  node={node(`folder-${index}`, MIME_FOLDER)}
                  parentFolderId={undefined}
                />
              ))}
            </>,
          ),
        ),
      );
      const observers = queryClient
        .getQueryCache()
        .findAll({ queryKey: wikiKeys.folderFilesPrefix })
        .reduce((count, query) => count + query.getObserversCount(), 0);
      expect(observers).toBe(0);
      expect(host.querySelector('[data-file-id="folder-0"]')).toHaveTextContent("1");
      expect(READ).not.toHaveBeenCalled();
      toggle();
      expect(host).toHaveTextContent("doc");
      expect(READ).not.toHaveBeenCalled();
      toggle();
      expect(host).not.toHaveTextContent("doc");
    },
  );

  it("skips hidden child counts for 500 incomplete folder headers and still lists expanded children", () => {
    const childrenByParent = new Map<string, DriveNode[]>();
    const readBucket = vi.spyOn(childrenByParent, "get");
    flushSync(() =>
      root.render(
        wrap(
          <>
            {Array.from({ length: 500 }, (_, index) => (
              <TreeNode
                depth={0}
                key={index}
                node={node(`folder-${index}`, MIME_FOLDER)}
                parentFolderId={undefined}
              />
            ))}
          </>,
          false,
          childrenByParent,
        ),
      ),
    );
    expect(readBucket).not.toHaveBeenCalled();
    queryClient.setQueryData(wikiKeys.folderFiles("folder-0"), [node("lazy-doc", MIME_DOC)]);
    toggle();
    expect(host).toHaveTextContent("lazy-doc");
    expect(readBucket).toHaveBeenCalledOnce();
  });

  it("does not schedule hover timers for 500 folders whose complete children are already available", () => {
    vi.useFakeTimers();
    flushSync(() =>
      root.render(
        wrap(
          <>
            {Array.from({ length: 500 }, (_, index) => (
              <TreeNode
                depth={0}
                key={index}
                node={node(`folder-${index}`, MIME_FOLDER)}
                parentFolderId={undefined}
              />
            ))}
          </>,
        ),
      ),
    );
    const schedule = vi.spyOn(window, "setTimeout");
    for (const trigger of host.querySelectorAll("[data-file-id]")) {
      trigger.dispatchEvent(
        new PointerEvent("pointerover", { bubbles: true, pointerType: "mouse" }),
      );
      trigger.dispatchEvent(
        new PointerEvent("pointerout", { bubbles: true, pointerType: "mouse" }),
      );
    }
    expect(schedule.mock.calls.filter(([, delay]) => delay === 80)).toHaveLength(0);
    expect(READ).not.toHaveBeenCalled();
  });

  it("admits lazy reads on expansion and releases the last observer on collapse", async () => {
    const pending = Promise.withResolvers<DriveNode[]>();
    READ.mockReturnValue(pending.promise);
    flushSync(() => root.render(wrap(folder(), false)));
    expect(
      queryClient.getQueryCache().findAll({ queryKey: wikiKeys.folderFilesPrefix }),
    ).toHaveLength(0);
    toggle();
    await vi.waitFor(() => expect(READ).toHaveBeenCalledOnce());
    expect(READ.mock.calls[0]?.[1].aborted).toBe(false);
    toggle();
    expect(READ.mock.calls[0]?.[1].aborted).toBe(true);
    expect(
      queryClient
        .getQueryCache()
        .find({ queryKey: wikiKeys.folderFiles("folder-0") })
        ?.getObserversCount(),
    ).toBe(0);
    pending.resolve([]);
  });

  it("preserves shared foreground reads and cached children through Strict Mode collapse/reopen", async () => {
    const pending = Promise.withResolvers<DriveNode[]>();
    READ.mockReturnValue(pending.promise);
    flushSync(() =>
      root.render(
        <StrictMode>
          {wrap(
            <>
              {folder()}
              <RetainedReader />
            </>,
            false,
          )}
        </StrictMode>,
      ),
    );
    toggle();
    await vi.waitFor(() => expect(READ).toHaveBeenCalledTimes(2));
    toggle();
    expect(READ.mock.calls.at(-1)?.[1].aborted).toBe(false);
    pending.resolve([node("loaded-doc", MIME_DOC)]);
    await vi.waitFor(() =>
      expect(queryClient.getQueryData(folderFilesQueryOptions("folder-0").queryKey)).toHaveLength(
        1,
      ),
    );
    toggle();
    await vi.waitFor(() => expect(host).toHaveTextContent("loaded-doc"));
    expect(READ).toHaveBeenCalledTimes(2);
  });

  it("renders optimistic creations, moves and cached children across complete/incomplete transitions", async () => {
    const cached = { ...node("cached-doc", MIME_DOC), parentId: "folder-0" };
    const moving = { ...node("moving-doc", MIME_DOC), parentId: "source-folder" };
    queryClient.setQueryData<DriveTree>(wikiKeys.driveTree("drive"), {
      driveId: "drive",
      files: [moving],
      filesComplete: false,
      folders: [node("folder-0", MIME_FOLDER)],
    });
    queryClient.setQueryData(wikiKeys.folderFiles("folder-0"), [cached]);
    flushSync(() =>
      root.render(
        <QueryClientProvider client={queryClient}>
          <SidebarProvider>
            <LiveTree />
          </SidebarProvider>
        </QueryClientProvider>,
      ),
    );
    expect(
      queryClient
        .getQueryCache()
        .find({ queryKey: wikiKeys.folderFiles("folder-0") })
        ?.getObserversCount(),
    ).toBe(0);
    toggle();
    expect(host).toHaveTextContent("cached-doc");
    injectNewFileIntoCaches(queryClient, {
      canEdit: true,
      driveId: "drive",
      id: "created-doc",
      mimeType: MIME_DOC,
      modifiedTime: undefined,
      name: "created-doc",
      parentFolderId: "folder-0",
    });
    await vi.waitFor(() => expect(host).toHaveTextContent("created-doc"));
    const rollback = optimisticMoveFile(
      queryClient,
      moving,
      { driveId: "drive", parentFolderId: "folder-0" },
      { driveId: "drive", parentFolderId: "source-folder" },
    );
    await vi.waitFor(() => expect(host).toHaveTextContent("moving-doc"));
    rollback();
    await vi.waitFor(() => expect(host).not.toHaveTextContent("moving-doc"));
    const tree = queryClient.getQueryData<DriveTree>(wikiKeys.driveTree("drive"));
    if (!tree) throw new TypeError("Tree fixture missing");
    queryClient.setQueryData<DriveTree>(wikiKeys.driveTree("drive"), {
      ...tree,
      files: [cached, ...tree.files],
      filesComplete: true,
    });
    await vi.waitFor(() =>
      expect(host.querySelector('[data-file-id="folder-0"]')).toHaveTextContent("2"),
    );
    expect(host).toHaveTextContent("cached-doc");
    expect(host).toHaveTextContent("created-doc");
    const currentTree = queryClient.getQueryData<DriveTree>(wikiKeys.driveTree("drive"));
    if (!currentTree) throw new TypeError("Current tree fixture missing");
    queryClient.setQueryData(wikiKeys.folderFiles("folder-0"), [
      { ...cached, id: "replacement-doc", name: "replacement-doc" },
    ]);
    queryClient.setQueryData<DriveTree>(wikiKeys.driveTree("drive"), {
      ...currentTree,
      filesComplete: false,
    });
    await vi.waitFor(() => expect(host).toHaveTextContent("replacement-doc"));
    expect(host).not.toHaveTextContent("created-doc");
    expect(READ).not.toHaveBeenCalled();
  });
});

function node(id: string, mimeType: string): DriveNode {
  return {
    id,
    mimeType,
    modifiedTime: undefined,
    name: id,
    parentId: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}

function folder(id = "folder-0") {
  return <TreeNode depth={0} node={node(id, MIME_FOLDER)} parentFolderId={undefined} />;
}

function RetainedReader() {
  useFolderFiles("folder-0", true);
  return null;
}

function LiveTree() {
  const index = useDriveTreeIndex(
    "drive",
    EMPTY_DOC_ICONS,
    EMPTY_FOLDER_ICONS,
    EMPTY_SORT_ORDERS,
    true,
  );
  const value: DriveSectionValue = {
    activeDriveId: null,
    childrenByParent: index.childrenByParent,
    docIconOverrides: index.docIconOverrides,
    driveId: "drive",
    filesByParent: index.filesByParent,
    iconOverrides: index.iconOverrides,
    optimisticByContainer: new Map(),
    pathname: "/",
    showNonDocFiles: true,
    sortOrderMap: index.sortOrderMap,
    treeFilesComplete: index.filesComplete,
  };
  return <DriveSectionProvider value={value}>{folder()}</DriveSectionProvider>;
}
