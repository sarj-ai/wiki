import { folderAncestorIds } from "@/lib/folder-ancestor-ids";
import { indexNodes } from "@/lib/wiki-indexes";
import type { DriveNode } from "@/types/wiki";
import { MIME_FOLDER } from "@/types/wiki";

describe("cached folder ancestry", () => {
  it("retains root-to-parent order and shares the immutable folder lookup", () => {
    const folders = [node("root"), node("parent", "root"), node("child", "parent")];
    expect(folderAncestorIds(folders, "child")).toEqual(["root", "parent"]);
    expect(indexNodes(folders)).toBe(indexNodes(folders));
    expect(folderAncestorIds(folders, "root")).toEqual([]);
    expect(folderAncestorIds(folders, "missing")).toEqual([]);
  });

  it("preserves a final unknown parent but stops cycles without repeating the selected node", () => {
    expect(folderAncestorIds([node("child", "unknown")], "child")).toEqual(["unknown"]);
    expect(folderAncestorIds([node("self", "self")], "self")).toEqual([]);
    expect(folderAncestorIds([node("a", "b"), node("b", "a")], "a")).toEqual(["b"]);
  });

  it("releases stale ancestry when immutable tree data changes or users differ", () => {
    const before = [node("child", "old")];
    const after = [node("child", "new")];
    expect(folderAncestorIds(before, "child")).toEqual(["old"]);
    expect(folderAncestorIds(after, "child")).toEqual(["new"]);
    expect(folderAncestorIds(before, "child")).toEqual(["old"]);
  });
});

function node(id: string, parentId?: string): DriveNode {
  return {
    id,
    name: id,
    mimeType: MIME_FOLDER,
    parentId,
    modifiedTime: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
