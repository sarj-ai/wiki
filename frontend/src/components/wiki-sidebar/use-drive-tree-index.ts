"use client";

import { useDriveTree } from "@/hooks/use-wiki-tree";
import {
  EMPTY_NODES,
  indexChildren,
  indexDocIcons,
  indexFolderIcons,
  indexSortOrders,
} from "@/lib/wiki-indexes";
import type { DocIcon, DriveNode, FolderIcon, SortOrder } from "@/types/wiki";

export function useDriveTreeIndex(
  driveId: string,
  docIcons: readonly DocIcon[],
  folderIcons: readonly FolderIcon[],
  sortOrders: readonly SortOrder[],
  enabled: boolean,
): DriveTreeIndex {
  const treeQuery = useDriveTree(driveId, enabled);

  const childrenByParent = indexChildren(treeQuery.data?.folders ?? EMPTY_NODES);
  const filesByParent = indexChildren(treeQuery.data?.files ?? EMPTY_NODES);
  const iconOverrides = indexFolderIcons(folderIcons);
  const docIconOverrides = indexDocIcons(docIcons);
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
