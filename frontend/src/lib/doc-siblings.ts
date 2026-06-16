import type { QueryClient } from "@tanstack/react-query";

import type { DriveNode, DriveTree, SortOrder } from "@/types/wiki";

import { resolveShortcutTarget } from "@/lib/shortcut-resolver";
import { wikiKeys } from "@/lib/wiki-keys";
import { compareNodeOrder, isFolderLikeNode, type KindedNode } from "@/lib/wiki-tree";
import { MIME_FORM, MIME_SHORTCUT } from "@/types/wiki";

export const SIBLING_RADIUS = 2;

export interface WarmTarget {
  fileId: string;
  mimeType: string;
  title: string;
}

export function computeWarmSiblings(queryClient: QueryClient, activeFileId: string): WarmTarget[] {
  const container = locateContainer(queryClient, activeFileId);
  if (container.length === 0) return [];
  const sortOrderMap = readSortOrderMap(queryClient);
  const ordered = container
    .map((node): KindedNode => ({ type: isFolderLikeNode(node) ? "folder" : "file", node }))
    .sort((a, b) => compareNodeOrder(a, b, sortOrderMap));
  const index = ordered.findIndex((k) => matchesFile(k.node, activeFileId));
  if (index < 0) return [];
  return [...collectSiblings(ordered, index, -1), ...collectSiblings(ordered, index, 1)];
}

function locateContainer(queryClient: QueryClient, fileId: string): DriveNode[] {
  for (const [, tree] of queryClient.getQueriesData<DriveTree>({
    queryKey: wikiKeys.driveTreePrefix,
  })) {
    if (!tree) continue;
    const all = [...tree.folders, ...tree.files];
    const hit = all.find((node) => matchesFile(node, fileId));
    if (!hit) continue;
    return hit.parentId
      ? gatherByParent(queryClient, hit.parentId)
      : all.filter((n) => !n.parentId);
  }
  for (const [, nodes] of queryClient.getQueriesData<DriveNode[]>({
    queryKey: wikiKeys.folderFilesPrefix,
  })) {
    const hit = nodes?.find((node) => matchesFile(node, fileId));
    if (hit?.parentId) return gatherByParent(queryClient, hit.parentId);
  }
  return [];
}

function gatherByParent(queryClient: QueryClient, parentId: string): DriveNode[] {
  const out = new Map<string, DriveNode>();
  for (const [, tree] of queryClient.getQueriesData<DriveTree>({
    queryKey: wikiKeys.driveTreePrefix,
  })) {
    if (!tree) continue;
    for (const node of [...tree.folders, ...tree.files]) {
      if (node.parentId === parentId) out.set(node.id, node);
    }
  }
  for (const node of queryClient.getQueryData<DriveNode[]>(wikiKeys.folderFiles(parentId)) ?? []) {
    out.set(node.id, node);
  }
  return [...out.values()];
}

function collectSiblings(ordered: KindedNode[], from: number, step: number): WarmTarget[] {
  const out: WarmTarget[] = [];
  for (
    let i = from + step;
    i >= 0 && i < ordered.length && out.length < SIBLING_RADIUS;
    i += step
  ) {
    const entry = ordered[i];
    const target = entry && toWarmTarget(entry);
    if (target) out.push(target);
  }
  return out;
}

function toWarmTarget(kinded: KindedNode): WarmTarget | null {
  if (kinded.type === "folder") return null;
  const route = resolveShortcutTarget(kinded.node);
  if (route.inaccessible || route.targetMimeType === MIME_FORM) return null;
  return {
    fileId: route.targetId,
    mimeType: route.isShortcut ? route.targetMimeType : kinded.node.mimeType,
    title: kinded.node.name,
  };
}

function matchesFile(node: DriveNode, fileId: string): boolean {
  if (node.mimeType === MIME_SHORTCUT) return node.shortcutTargetId === fileId;
  return node.id === fileId;
}

function readSortOrderMap(queryClient: QueryClient): Map<string, number> {
  const map = new Map<string, number>();
  for (const entry of queryClient.getQueryData<SortOrder[]>(wikiKeys.sortOrders) ?? []) {
    map.set(entry.driveId, entry.sortOrder);
  }
  return map;
}
