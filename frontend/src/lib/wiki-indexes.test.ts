import {
  indexChildren,
  indexDocIcons,
  indexFolderIcons,
  indexSortOrders,
} from "@/lib/wiki-indexes";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

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
});
