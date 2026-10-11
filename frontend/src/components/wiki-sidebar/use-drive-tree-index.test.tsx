import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { wikiKeys } from "@/lib/wiki-keys";
import type { DocIcon, DriveNode, DriveTree, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { useDriveTreeIndex } from "./use-drive-tree-index";

const READ = vi.hoisted(() => vi.fn<() => Promise<DriveTree>>());
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getDriveFolders: READ } }));

describe("collapsed drive cold indexes", () => {
  let root: Root;
  let client: QueryClient;
  let result: ReturnType<typeof useDriveTreeIndex>;
  let reads: { parents: number; overlays: number };
  let tree: DriveTree;
  let docIcons: DocIcon[];
  let folderIcons: FolderIcon[];
  let sortOrders: SortOrder[];

  beforeEach(() => {
    reads = { parents: 0, overlays: 0 };
    READ.mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    tree = {
      driveId: "drive",
      filesComplete: false,
      folders: Array.from({ length: 10_000 }, (_, index) => node(`folder-${index}`, MIME_FOLDER)),
      files: Array.from({ length: 10_000 }, (_, index) => node(`file-${index}`, MIME_DOC)),
    };
    docIcons = Array.from({ length: 1000 }, (_, index) => ({
      get driveId() {
        reads.overlays++;
        return `file-${index}`;
      },
      icon: "📄",
      coverUrl: undefined,
    }));
    folderIcons = Array.from({ length: 1000 }, (_, index) => ({
      get folderDriveId() {
        reads.overlays++;
        return `folder-${index}`;
      },
      icon: "📂",
    }));
    sortOrders = Array.from({ length: 1000 }, (_, index) => ({
      get driveId() {
        reads.overlays++;
        return `file-${index}`;
      },
      sortOrder: index,
    }));
    client.setQueryData(wikiKeys.driveTree("drive"), tree);
    root = createRoot(document.createElement("div"));
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    client.clear();
  });

  function node(id: string, mimeType: string, parentId?: string): DriveNode {
    return {
      id,
      name: id,
      mimeType,
      get parentId() {
        reads.parents++;
        return parentId;
      },
      modifiedTime: undefined,
      shortcutInaccessible: false,
      shortcutTargetDriveId: undefined,
      shortcutTargetDriveName: undefined,
      shortcutTargetId: undefined,
      shortcutTargetMimeType: undefined,
    };
  }

  function Probe({ enabled }: { enabled: boolean }) {
    result = useDriveTreeIndex("drive", docIcons, folderIcons, sortOrders, enabled);
    return null;
  }

  function render(enabled: boolean) {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe enabled={enabled} />
        </QueryClientProvider>,
      ),
    );
  }

  it("does not visit 20,000 cached nodes or 3,000 overlays while collapsed", () => {
    render(false);
    expect(reads).toEqual({ parents: 0, overlays: 0 });
    expect(result.childrenByParent.size).toBe(0);
    expect(result.filesByParent.size).toBe(0);
    expect(result.docIconOverrides.size).toBe(0);
    expect(result.iconOverrides.size).toBe(0);
    expect(result.sortOrderMap.size).toBe(0);
    expect(result.treeQuery.data).toBe(tree);
    expect(result.filesComplete).toBe(false);
    expect(READ).not.toHaveBeenCalled();
  });

  it("builds current indexes on expansion and reuses them on subsequent renders", () => {
    render(false);
    render(true);
    expect(reads).toEqual({ parents: 20_000, overlays: 3000 });
    expect(result.childrenByParent.get("__root__")).toHaveLength(10_000);
    expect(result.filesByParent.get("__root__")).toHaveLength(10_000);
    expect(result.docIconOverrides.get("file-0")).toBe("📄");
    expect(result.iconOverrides.get("folder-0")).toBe("📂");
    expect(result.sortOrderMap.get("file-0")).toBe(0);
    const expanded = result;
    render(false);
    render(true);
    expect(result.childrenByParent).toBe(expanded.childrenByParent);
    expect(result.filesByParent).toBe(expanded.filesByParent);
    expect(reads).toEqual({ parents: 20_000, overlays: 3000 });
    expect(result.filesComplete).toBe(false);
    expect(READ).not.toHaveBeenCalled();
  });
});
