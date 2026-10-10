"use client";

import { useDocIcons, useDriveTree } from "@/hooks/use-wiki-tree";
import {
  EMPTY_DOC_ICONS,
  EMPTY_NODES,
  indexChildren,
  indexDocIcons,
  indexFolderIcons,
  indexSortOrders,
} from "@/lib/wiki-indexes";
import type { DriveNode, FolderIcon, SortOrder } from "@/types/wiki";

export function useDriveTreeIndex(
  driveId: string,
  folderIcons: FolderIcon[],
  sortOrders: SortOrder[],
  enabled: boolean,
): DriveTreeIndex {
  const treeQuery = useDriveTree(driveId, enabled);
  const docIconsQuery = useDocIcons();

  const childrenByParent = indexChildren(treeQuery.data?.folders ?? EMPTY_NODES);
  const filesByParent = indexChildren(treeQuery.data?.files ?? EMPTY_NODES);
  const iconOverrides = indexFolderIcons(folderIcons);
  const docIconOverrides = indexDocIcons(docIconsQuery.data ?? EMPTY_DOC_ICONS);
  const sortOrderMap = indexSortOrders(sortOrders);

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
