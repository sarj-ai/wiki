"use client";

import { useDocIcons, useDriveTree } from "@/hooks/use-wiki-tree";
import { buildChildrenIndex } from "@/lib/wiki-tree";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";

export function useDriveTreeIndex(
  driveId: string,
  folderIcons: FolderIcon[],
  sortOrders: SortOrder[],
): DriveTreeIndex {
  const treeQuery = useDriveTree(driveId);
  const docIconsQuery = useDocIcons();

  const childrenByParent = buildChildrenIndex(treeQuery.data?.folders ?? []);
  const filesByParent = buildChildrenIndex(treeQuery.data?.files ?? []);
  const iconOverrides = mapByKey(
    folderIcons,
    (i) => i.folderDriveId,
    (i) => i.icon,
  );
  const docIconOverrides = mapByKey<DocIcon, string | undefined>(
    docIconsQuery.data ?? [],
    (d) => d.driveId,
    (d) => d.icon,
  );
  const sortOrderMap = mapByKey(
    sortOrders,
    (s) => s.driveId,
    (s) => s.sortOrder,
  );

  const filesComplete = treeQuery.data?.filesComplete ?? true;

  return {
    childrenByParent,
    docIconOverrides,
    filesByParent,
    filesComplete,
    iconOverrides,
    sortOrderMap,
    treeQuery,
  };
}

interface DriveTreeIndex {
  childrenByParent: Map<string, DriveNode[]>;
  docIconOverrides: Map<string, string | undefined>;
  filesByParent: Map<string, DriveNode[]>;
  filesComplete: boolean;
  iconOverrides: Map<string, string | undefined>;
  sortOrderMap: Map<string, number>;
  treeQuery: ReturnType<typeof useDriveTree>;
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
