import { describe, expect, it } from "vitest";

import type { DriveNode } from "@/types/navigator";

import { applyOptimisticOrder, buildChildrenIndex, compareNodeOrder } from "@/lib/navigator-tree";
import { MIME_DOC, MIME_FOLDER } from "@/types/navigator";

function folder(id: string, parentId: string | null, name = id): DriveNode {
  return {
    id,
    name,
    mimeType: MIME_FOLDER,
    parentId,
    modifiedTime: null,
  };
}
function file(id: string, name = id): DriveNode {
  return {
    id,
    name,
    mimeType: MIME_DOC,
    parentId: null,
    modifiedTime: null,
  };
}

describe("buildChildrenIndex", () => {
  it("groups folders by their parentId, keying root under __root__", () => {
    const idx = buildChildrenIndex([
      folder("root1", null),
      folder("root2", null),
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
    const a = { type: "folder" as const, node: folder("pinned-b", null) };
    const b = { type: "folder" as const, node: folder("pinned-a", null) };
    expect(compareNodeOrder(a, b, orders)).toBeGreaterThan(0);
  });

  it("places items with sort_order before items without", () => {
    const a = { type: "folder" as const, node: folder("pinned-a", null) };
    const b = { type: "folder" as const, node: folder("unpinned", null) };
    expect(compareNodeOrder(a, b, orders)).toBeLessThan(0);
    expect(compareNodeOrder(b, a, orders)).toBeGreaterThan(0);
  });

  it("falls back to folders-before-files when neither is pinned", () => {
    const a = { type: "file" as const, node: file("a") };
    const b = { type: "folder" as const, node: folder("b", null) };
    expect(compareNodeOrder(a, b, orders)).toBeGreaterThan(0);
    expect(compareNodeOrder(b, a, orders)).toBeLessThan(0);
  });

  it("falls back to alphabetical inside the same kind", () => {
    const a = { type: "folder" as const, node: folder("zzz", null, "Zebra") };
    const b = { type: "folder" as const, node: folder("aaa", null, "Apple") };
    expect(compareNodeOrder(a, b, orders)).toBeGreaterThan(0);
  });
});

describe("applyOptimisticOrder", () => {
  const items = [
    { type: "folder" as const, node: folder("a", null) },
    { type: "folder" as const, node: folder("b", null) },
    { type: "file" as const, node: file("c") },
  ];

  it("returns items unchanged when no order is supplied", () => {
    expect(applyOptimisticOrder(items, null)).toBe(items);
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
