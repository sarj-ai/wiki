"use client";

import { useDriveTree } from "@/hooks/use-wiki-tree";
import {
  EMPTY_DOC_ICONS,
  EMPTY_FOLDER_ICONS,
  EMPTY_NODES,
  EMPTY_SORT_ORDERS,
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

  const childrenByParent = indexChildren(
    enabled ? (treeQuery.data?.folders ?? EMPTY_NODES) : EMPTY_NODES,
  );
  const filesByParent = indexChildren(
    enabled ? (treeQuery.data?.files ?? EMPTY_NODES) : EMPTY_NODES,
  );
  const iconOverrides = indexFolderIcons(enabled ? folderIcons : EMPTY_FOLDER_ICONS);
  const docIconOverrides = indexDocIcons(enabled ? docIcons : EMPTY_DOC_ICONS);
  const sortOrderMap = indexSortOrders(enabled ? sortOrders : EMPTY_SORT_ORDERS);

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
