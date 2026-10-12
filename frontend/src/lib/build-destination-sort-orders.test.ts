import { buildDestinationSortOrders } from "@/lib/build-destination-sort-orders";
import type { DriveNode, DriveTree, SortOrder } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

const MOVED = node("moved", MIME_DOC);

describe("destination move ordering", () => {
  it("uses only complete-tree root siblings rather than every file in the drive", () => {
    const tree = fixtureTree();
    expect(order(tree, "drive", "root-doc", "below")).toEqual([
      "root-folder",
      "root-shortcut",
      "root-doc",
      "moved",
    ]);
  });

  it("uses complete-tree nested files without a redundant lazy folder cache", () => {
    expect(order(fixtureTree(), "root-folder", "nested-doc", "above")).toEqual([
      "nested-folder",
      "moved",
      "nested-doc",
    ]);
  });

  it.each(["drive", "root-folder"])(
    "uses incomplete folder cache files and excludes duplicate folder-like nodes for %s",
    (folderId) => {
      const tree = { ...fixtureTree(), files: [], filesComplete: false };
      const lazy = [
        node("lazy-doc", MIME_DOC),
        node("root-folder", MIME_FOLDER),
        { ...node("root-shortcut", MIME_SHORTCUT), shortcutTargetMimeType: MIME_FOLDER },
      ];
      const result = buildDestinationSortOrders({
        driveId: "drive",
        folderId,
        folderFiles: lazy,
        movedNode: MOVED,
        positionHint: { rowId: "lazy-doc", intent: "below" },
        sortOrders: [],
        tree,
      });
      expect(result?.map((item) => item.driveId)).toEqual(
        folderId === "drive"
          ? ["root-folder", "root-shortcut", "lazy-doc", "moved"]
          : ["nested-folder", "lazy-doc", "moved"],
      );
    },
  );

  it("keeps mixed custom ranks and removes an already-present moved node", () => {
    const tree = fixtureTree();
    tree.files.push(MOVED);
    const ranks: SortOrder[] = [
      { driveId: "root-doc", sortOrder: 1 },
      { driveId: "root-folder", sortOrder: 2 },
    ];
    expect(order(tree, "drive", "root-folder", "above", ranks)).toEqual([
      "root-doc",
      "moved",
      "root-folder",
      "root-shortcut",
    ]);
  });

  it.each([
    ["anchor-a", "above", ["moved", "anchor-a", "anchor-z"]],
    ["anchor-a", "below", ["anchor-a", "moved", "anchor-z"]],
    ["anchor-z", "above", ["anchor-a", "moved", "anchor-z"]],
    ["anchor-z", "below", ["anchor-a", "anchor-z", "moved"]],
  ] as const)("inserts %s %s with consecutive final ranks", (rowId, intent, expected) => {
    const tree: DriveTree = {
      driveId: "drive",
      filesComplete: true,
      folders: [],
      files: [node("anchor-z", MIME_DOC), MOVED, node("anchor-a", MIME_DOC)],
    };
    expect(order(tree, "drive", rowId, intent)).toEqual(expected);
  });

  it("does not persist a rank list for an absent destination anchor", () => {
    expect(order(fixtureTree(), "root-folder", "root-doc", "above")).toBeNull();
  });

  it("uses current names when immutable root or nested metadata changes", () => {
    const tree = fixtureTree();
    const second = node("second-doc", MIME_DOC, "root-folder");
    tree.files.push({ ...second, name: "A" });
    expect(order(tree, "root-folder", "nested-doc", "below")).toEqual([
      "nested-folder",
      "second-doc",
      "nested-doc",
      "moved",
    ]);
    const updated = { ...tree, files: tree.files.map((item) => ({ ...item, name: item.id })) };
    expect(order(updated, "root-folder", "nested-doc", "below")).toEqual([
      "nested-folder",
      "nested-doc",
      "moved",
      "second-doc",
    ]);
  });

  it("uses a known lazy listing when no tree has been loaded", () => {
    const result = buildDestinationSortOrders({
      driveId: "drive",
      folderId: "drive",
      folderFiles: [node("lazy-doc", MIME_DOC)],
      movedNode: MOVED,
      positionHint: { rowId: "lazy-doc", intent: "above" },
      sortOrders: [],
      tree: undefined,
    });
    expect(result).toEqual([
      { driveId: "moved", sortOrder: 1 },
      { driveId: "lazy-doc", sortOrder: 2 },
    ]);
  });
});

function order(
  tree: DriveTree,
  folderId: string,
  rowId: string,
  intent: "above" | "below",
  sortOrders: SortOrder[] = [],
): null | string[] {
  const result = buildDestinationSortOrders({
    driveId: "drive",
    folderFiles: [],
    folderId,
    movedNode: MOVED,
    positionHint: { rowId, intent },
    sortOrders,
    tree,
  });
  if (result)
    expect(result.map((item) => item.sortOrder)).toEqual(result.map((_, index) => index + 1));
  return result?.map((item) => item.driveId) ?? null;
}

function fixtureTree(): DriveTree {
  return {
    driveId: "drive",
    filesComplete: true,
    folders: [
      node("root-folder", MIME_FOLDER),
      { ...node("root-shortcut", MIME_SHORTCUT), shortcutTargetMimeType: MIME_FOLDER },
      node("nested-folder", MIME_FOLDER, "root-folder"),
    ],
    files: [node("root-doc", MIME_DOC), node("nested-doc", MIME_DOC, "root-folder")],
  };
}

function node(id: string, mimeType: string, parentId?: string): DriveNode {
  return {
    id,
    name: id,
    mimeType,
    parentId,
    modifiedTime: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
