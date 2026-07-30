import type { DriveNode } from "@/types/wiki";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

type NodeKind = "file" | "folder";

/** Children-index key for nodes whose parent is the drive root itself. */
export const TREE_ROOT_ID = "__root__";

export function isFolderLikeNode(node: DriveNode): boolean {
  return (
    node.mimeType === MIME_FOLDER ||
    (node.mimeType === MIME_SHORTCUT && node.shortcutTargetMimeType === MIME_FOLDER)
  );
}

export function buildChildrenIndex(folders: DriveNode[]): Map<string, DriveNode[]> {
  const map = new Map<string, DriveNode[]>();
  for (const folder of folders) {
    const key = folder.parentId ?? TREE_ROOT_ID;
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
  items: KindedNode[],
  order: null | readonly string[],
): KindedNode[] {
  if (!order) return items;
  const byId = new Map<string, KindedNode>(items.map((item) => [item.node.id, item]));
  const ordered: KindedNode[] = [];
  const seen = new Set<string>();
  for (const id of order) {
    const hit = byId.get(id);
    if (hit) {
      ordered.push(hit);
      seen.add(id);
    }
  }
  for (const item of items) {
    if (!seen.has(item.node.id)) ordered.push(item);
  }
  return ordered;
}
