import { describe, expect, it } from "vitest";

import {
  applyOptimisticOrder,
  buildChildrenIndex,
  buildSiblingItems,
  compareNodeOrder,
  orderSiblingItems,
} from "@/lib/wiki-tree";
import type { DriveNode } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

function folder(id: string, parentId?: string, name = id): DriveNode {
  return {
    id,
    mimeType: MIME_FOLDER,
    modifiedTime: undefined,
    name,
    parentId,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
function file(id: string, name = id): DriveNode {
  return {
    id,
    mimeType: MIME_DOC,
    modifiedTime: undefined,
    name,
    parentId: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}

describe("buildChildrenIndex", () => {
  it("groups folders by their parentId, keying root under __root__", () => {
    const idx = buildChildrenIndex([
      folder("root1"),
      folder("root2"),
      folder("child1", "root1"),
      folder("child2", "root1"),
      folder("child3", "root2"),
    ]);
    expect(idx.get("__root__")?.map((n) => n.id)).toEqual(["root1", "root2"]);
    expect(idx.get("root1")?.map((n) => n.id)).toEqual(["child1", "child2"]);
    expect(idx.get("root2")?.map((n) => n.id)).toEqual(["child3"]);
  });

  it("returns an empty map for an empty input", () => {
    expect(buildChildrenIndex([]).size).toBe(0);
  });
});

describe("compareNodeOrder", () => {
  const orders = new Map<string, number>([
    ["pinned-a", 1],
    ["pinned-b", 2],
  ]);

  it("orders by sort_order when both items have one", () => {
    const a = { node: folder("pinned-b"), type: "folder" as const };
    const b = { node: folder("pinned-a"), type: "folder" as const };
    expect(compareNodeOrder(a, b, orders)).toBeGreaterThan(0);
  });

  it("places items with sort_order before items without", () => {
    const a = { node: folder("pinned-a"), type: "folder" as const };
    const b = { node: folder("unpinned"), type: "folder" as const };
    expect(compareNodeOrder(a, b, orders)).toBeLessThan(0);
    expect(compareNodeOrder(b, a, orders)).toBeGreaterThan(0);
  });

  it("falls back to folders-before-files when neither is pinned", () => {
    const a = { node: file("a"), type: "file" as const };
    const b = { node: folder("b"), type: "folder" as const };
    expect(compareNodeOrder(a, b, orders)).toBeGreaterThan(0);
    expect(compareNodeOrder(b, a, orders)).toBeLessThan(0);
  });

  it("falls back to alphabetical inside the same kind", () => {
    const a = { node: folder("zzz", undefined, "Zebra"), type: "folder" as const };
    const b = { node: folder("aaa", undefined, "Apple"), type: "folder" as const };
    expect(compareNodeOrder(a, b, orders)).toBeGreaterThan(0);
  });
});

describe("applyOptimisticOrder", () => {
  const items = [
    { node: folder("a"), type: "folder" as const },
    { node: folder("b"), type: "folder" as const },
    { node: file("c"), type: "file" as const },
  ];

  it("returns items unchanged when no order is supplied", () => {
    expect(applyOptimisticOrder(items, null)).toBe(items);
  });

  it("shares unchanged empty and exact optimistic orders", () => {
    expect(applyOptimisticOrder(items, [])).toBe(items);
    expect(applyOptimisticOrder(items, ["a", "b", "c"])).toBe(items);
  });

  it("reorders items to match the supplied id list", () => {
    const out = applyOptimisticOrder(items, ["c", "a", "b"]);
    expect(out.map((i) => i.node.id)).toEqual(["c", "a", "b"]);
  });

  it("appends items not present in the optimistic order", () => {
    const out = applyOptimisticOrder(items, ["b", "a"]);
    expect(out.map((i) => i.node.id)).toEqual(["b", "a", "c"]);
  });

  it("ignores ids in the order that have no matching item", () => {
    const out = applyOptimisticOrder(items, ["ghost", "a"]);
    expect(out.map((i) => i.node.id)).toEqual(["a", "b", "c"]);
  });
});

describe("sibling construction and root optimistic order", () => {
  it("retains folders and files as separate kinds without mutating provider nodes", () => {
    const folders = [folder("f")];
    const files = [file("d")];
    expect(buildSiblingItems(folders, files)).toEqual([
      { node: folders[0], type: "folder" },
      { node: files[0], type: "file" },
    ]);
    expect(folders).toHaveLength(1);
    expect(files).toHaveLength(1);
  });

  it("does not collate a complete optimistic root order", () => {
    const items = buildSiblingItems([folder("z")], [file("b"), file("a")]);
    const compare = vi.spyOn(String.prototype, "localeCompare");
    try {
      expect(
        orderSiblingItems(items, new Map(), ["a", "z", "b"]).map((item) => item.node.id),
      ).toEqual(["a", "z", "b"]);
      expect(compare).not.toHaveBeenCalled();
    } finally {
      compare.mockRestore();
    }
  });

  it("keeps the sorted fallback for a partial or stale optimistic root order", () => {
    const items = buildSiblingItems([folder("z")], [file("b"), file("a")]);
    expect(
      orderSiblingItems(items, new Map(), ["missing", "b"]).map((item) => item.node.id),
    ).toEqual(["b", "z", "a"]);
    const ranked = buildSiblingItems([folder("z")], [file("b"), file("a")]);
    expect(
      orderSiblingItems(ranked, new Map([["a", 1]]), ["b"]).map((item) => item.node.id),
    ).toEqual(["b", "a", "z"]);
  });
});
