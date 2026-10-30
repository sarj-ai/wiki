import {
  indexChildren,
  indexDestinationFolders,
  indexDocIcons,
  indexFolderIcons,
  indexSidebarNodes,
  indexSortOrders,
  indexVisibleDocuments,
} from "@/lib/wiki-indexes";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER, MIME_HTML, MIME_SHORTCUT } from "@/types/wiki";

describe("shared immutable sidebar indexes", () => {
  it("reuses an index across Drive sections until its query data changes", () => {
    const nodes: DriveNode[] = [
      {
        id: "file-1",
        name: "Plan",
        mimeType: MIME_DOC,
        parentId: "folder-1",
        modifiedTime: undefined,
        shortcutInaccessible: false,
        shortcutTargetDriveId: undefined,
        shortcutTargetDriveName: undefined,
        shortcutTargetId: undefined,
        shortcutTargetMimeType: undefined,
      },
    ];
    const original = indexChildren(nodes);
    expect(indexChildren(nodes)).toBe(original);
    const first = nodes[0];
    if (!first) throw new Error("Node fixture is missing");
    const updated = indexChildren([...nodes, { ...first, id: "file-2" }]);
    expect(updated).not.toBe(original);
    expect(original.get("folder-1")).toHaveLength(1);
    expect(updated.get("folder-1")).toHaveLength(2);
  });

  it("shares overlays without mixing separately scoped query arrays", () => {
    const folders: FolderIcon[] = [{ folderDriveId: "folder", icon: "📚" }];
    const docs: DocIcon[] = [{ driveId: "file", icon: "🚀", coverUrl: undefined }];
    const sorts: SortOrder[] = [{ driveId: "file", sortOrder: 1.5 }];
    expect(indexFolderIcons(folders)).toBe(indexFolderIcons(folders));
    expect(indexDocIcons(docs)).toBe(indexDocIcons(docs));
    expect(indexSortOrders(sorts)).toBe(indexSortOrders(sorts));
    expect(indexFolderIcons([{ folderDriveId: "folder", icon: "🔒" }]).get("folder")).toBe("🔒");
    expect(indexFolderIcons(folders).get("folder")).toBe("📚");
  });

  it("reuses destination folders while excluding shortcuts and invalidating changed arrays", () => {
    const real = node("real", MIME_FOLDER);
    const shortcut = node("shortcut", MIME_SHORTCUT, MIME_FOLDER);
    const folders = [real, shortcut];
    const first = indexDestinationFolders(folders);
    expect(first.get("__root__")).toEqual([real]);
    expect(indexDestinationFolders(folders)).toBe(first);
    expect(indexDestinationFolders([{ ...real, name: "Renamed" }]).get("__root__")?.[0]?.name).toBe(
      "Renamed",
    );
    expect(first.get("__root__")?.[0]?.name).toBe("real");
  });

  it("classifies lazy folder shortcuts once and preserves the existing document filter", () => {
    const nodes = [
      node("folder", MIME_FOLDER),
      node("folder-shortcut", MIME_SHORTCUT, MIME_FOLDER),
      node("doc", MIME_DOC),
      node("html", MIME_HTML),
      node("doc-shortcut", MIME_SHORTCUT, MIME_DOC),
    ];
    const result = indexSidebarNodes(nodes);
    expect(result.folders.map((item) => item.id)).toEqual(["folder", "folder-shortcut"]);
    expect(result.files.map((item) => item.id)).toEqual(["doc", "html", "doc-shortcut"]);
    expect(indexSidebarNodes(nodes)).toBe(result);
    expect(indexVisibleDocuments(result.files).map((item) => item.id)).toEqual(["doc", "html"]);
    expect(indexVisibleDocuments(result.files)).toBe(indexVisibleDocuments(result.files));
  });
});

function node(id: string, mimeType: string, targetMimeType?: string): DriveNode {
  return {
    id,
    name: id,
    mimeType,
    parentId: undefined,
    modifiedTime: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: targetMimeType,
  };
}
