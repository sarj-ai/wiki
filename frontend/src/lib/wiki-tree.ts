import type { DriveNode } from "@/types/wiki";

import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

/**
 * True when ``node`` should render as a folder in the sidebar — either
 * a native Drive folder or a shortcut whose target is a folder. Used
 * in both the drive root (drive-section) and inside expanded folders
 * (tree-node) to keep folder vs file partitioning consistent. The
 * sidebar tree walk surfaces folder-target shortcuts in its folder
 * list; the lazy children endpoint also returns them. Both call sites
 * must use this predicate to strip duplicates from the lazy result,
 * otherwise the shortcut renders twice (once as folder, once as file)
 * with the same React key.
 */
export function isFolderLikeNode(node: DriveNode): boolean {
  return (
    node.mimeType === MIME_FOLDER ||
    (node.mimeType === MIME_SHORTCUT && node.shortcutTargetMimeType === MIME_FOLDER)
  );
}

/**
 * Pure tree helpers shared by the sidebar's DriveSection / TreeNode
 * components and the move-file dialog. Lives in ``lib/`` so the
 * helpers are unit-testable and free of React, dnd-kit, or React
 * Query coupling — callers pass already-fetched data in.
 */

/**
 * Group folder nodes by their ``parentId`` so an O(1) lookup gives
 * the immediate children of any folder. ``__root__`` keys top-level
 * children (parentId === null) since Map<null, …> doesn't work and
 * ``""`` is a valid string id.
 */
export function buildChildrenIndex(folders: DriveNode[]): Map<string, DriveNode[]> {
  const map = new Map<string, DriveNode[]>();
  for (const folder of folders) {
    const key = folder.parentId ?? "__root__";
    const bucket = map.get(key);
    if (bucket) bucket.push(folder);
    else map.set(key, [folder]);
  }
  return map;
}

type NodeKind = "folder" | "file";
export interface KindedNode {
  type: NodeKind;
  node: DriveNode;
}

/**
 * Comparator for the sidebar's row ordering. Items with an explicit
 * ``sort_order`` land first, in that order; the rest fall back to
 * "folders before files, then alphabetical by name". Single sort pass
 * across folders AND files together is intentional — interleaving
 * after a user-driven drop must survive the round-trip, otherwise the
 * displayed order would revert from "interleaved" back to "folders
 * first" the moment the optimistic state clears, and dnd-kit's FLIP
 * would bounce items to their new rows.
 */
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

/**
 * Reorder a freshly sorted item list using a per-container optimistic
 * id list captured on drop. Items present in ``order`` keep its order;
 * items that arrive after the drag (rare race — folder gained children
 * mid-drop) get appended. Used by both the drive root and every
 * subfolder so reorders paint in the same frame as the drop instead
 * of waiting for the sortOrderMap prop to propagate down.
 */
export function applyOptimisticOrder(
  items: KindedNode[],
  order: readonly string[] | null,
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
