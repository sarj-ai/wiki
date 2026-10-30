import type { DriveNode } from "@/types/wiki";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

export function isFolderLikeNode(node: DriveNode): boolean {
  return (
    node.mimeType === MIME_FOLDER ||
    (node.mimeType === MIME_SHORTCUT && node.shortcutTargetMimeType === MIME_FOLDER)
  );
}

export function buildChildrenIndex(
  folders: readonly DriveNode[],
  excludedMimeType?: string,
): Map<string, DriveNode[]> {
  const map = new Map<string, DriveNode[]>();
  for (const folder of folders) {
    if (excludedMimeType !== undefined && folder.mimeType === excludedMimeType) continue;
    const key = folder.parentId ?? "__root__";
    const bucket = map.get(key);
    if (bucket) bucket.push(folder);
    else map.set(key, [folder]);
  }
  return map;
}

export interface KindedNode {
  node: DriveNode;
  type: NodeKind;
}

export function buildSiblingItems(
  folders: readonly DriveNode[],
  files: readonly DriveNode[],
): KindedNode[] {
  return [
    ...folders.map((node): KindedNode => ({ node, type: "folder" })),
    ...files.map((node): KindedNode => ({ node, type: "file" })),
  ];
}

export function orderSiblingItems(
  items: KindedNode[],
  sortOrderMap: Map<string, number>,
  optimisticOrder: null | readonly string[],
): readonly KindedNode[] {
  if (optimisticOrder) return applyOptimisticOrder(items, optimisticOrder, sortOrderMap);
  return items.toSorted((a, b) => compareNodeOrder(a, b, sortOrderMap));
}

export function compareNodeOrder(
  a: KindedNode,
  b: KindedNode,
  sortOrderMap: Map<string, number>,
): number {
  const ao = sortOrderMap.get(a.node.id);
  const bo = sortOrderMap.get(b.node.id);
  if (ao !== undefined && bo !== undefined) return ao - bo;
  if (ao !== undefined) return -1;
  if (bo !== undefined) return 1;
  if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
  return a.node.name.localeCompare(b.node.name);
}

export function applyOptimisticOrder(
  items: readonly KindedNode[],
  order: null | readonly string[],
  fallbackSortOrderMap?: Map<string, number>,
): readonly KindedNode[] {
  if (!order || order.length === 0) {
    return fallbackSortOrderMap
      ? items.toSorted((a, b) => compareNodeOrder(a, b, fallbackSortOrderMap))
      : items;
  }
  if (order.length === items.length && order.every((id, index) => items[index]?.node.id === id)) {
    return items;
  }
  const byId = new Map<string, KindedNode>();
  for (const item of items) byId.set(item.node.id, item);
  const ordered: KindedNode[] = [];
  const seen = new Set<string>();
  for (const id of order) {
    const hit = byId.get(id);
    if (hit) {
      ordered.push(hit);
      seen.add(id);
    }
  }
  if (fallbackSortOrderMap) {
    const remaining = items.filter((item) => !seen.has(item.node.id));
    const sorted = remaining.toSorted((a, b) => compareNodeOrder(a, b, fallbackSortOrderMap));
    for (const item of sorted) ordered.push(item);
  } else {
    for (const item of items) {
      if (!seen.has(item.node.id)) ordered.push(item);
    }
  }
  return ordered;
}

type NodeKind = "file" | "folder";
