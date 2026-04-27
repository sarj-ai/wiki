"use client";

import { useMemo } from "react";

import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/navigator";

import { useDocIcons, useDriveTree } from "@/hooks/use-navigator-tree";
import { buildChildrenIndex } from "@/lib/navigator-tree";

export interface DriveTreeIndex {
  childrenByParent: Map<string, DriveNode[]>;
  iconOverrides: Map<string, string | null>;
  docIconOverrides: Map<string, string | null>;
  sortOrderMap: Map<string, number>;
  treeQuery: ReturnType<typeof useDriveTree>;
}

/**
 * Centralises every derived Map the sidebar's render path consults.
 * Pulls the drive tree, doc icons, folder icons, and sort orders from
 * React Query and projects them into Map shapes for O(1) lookups
 * inside ``DriveSection`` and every ``TreeNode``.
 *
 * Exposing the underlying ``treeQuery`` lets the consumer surface
 * loading / error states without re-subscribing to the same query.
 */
export function useDriveTreeIndex(
  driveId: string,
  folderIcons: FolderIcon[],
  sortOrders: SortOrder[],
): DriveTreeIndex {
  const treeQuery = useDriveTree(driveId);
  const docIconsQuery = useDocIcons();

  const childrenByParent = useMemo(
    () => buildChildrenIndex(treeQuery.data?.folders ?? []),
    [treeQuery.data],
  );
  const iconOverrides = useMemo(
    () => mapByKey(folderIcons, (i) => i.folderDriveId, (i) => i.icon),
    [folderIcons],
  );
  const docIconOverrides = useMemo(
    () => mapByKey<DocIcon, string | null>(docIconsQuery.data ?? [], (d) => d.driveId, (d) => d.icon),
    [docIconsQuery.data],
  );
  const sortOrderMap = useMemo(
    () => mapByKey(sortOrders, (s) => s.driveId, (s) => s.sortOrder),
    [sortOrders],
  );

  return { childrenByParent, iconOverrides, docIconOverrides, sortOrderMap, treeQuery };
}

function mapByKey<T, V>(
  items: readonly T[],
  key: (item: T) => string,
  value: (item: T) => V,
): Map<string, V> {
  const map = new Map<string, V>();
  for (const item of items) map.set(key(item), value(item));
  return map;
}
