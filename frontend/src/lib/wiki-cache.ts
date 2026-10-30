import type { QueryClient, QueryKey } from "@tanstack/react-query";

import { captureMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

export interface CachedNodeMeta {
  mimeType: string;
  modifiedTime?: string;
  name: string;
}

// Pending document renders can request the same hint repeatedly. Keep only the
// last lookup per immutable query array, so misses are cheap without indexing
// every node or retaining query data after eviction.
const NODE_META_CACHE = new WeakMap<
  readonly DriveNode[],
  { fileId: string; meta: CachedNodeMeta | null }
>();

export function findCachedNodeMeta(qc: QueryClient, fileId: string): CachedNodeMeta | null {
  for (const [, nodes] of qc.getQueriesData<DriveNode[]>({
    queryKey: wikiKeys.folderFilesPrefix,
  })) {
    const meta = metaFromNodes(nodes, fileId);
    if (meta) return meta;
  }
  for (const [, tree] of qc.getQueriesData<DriveTree>({ queryKey: wikiKeys.driveTreePrefix })) {
    const meta = tree && (metaFromNodes(tree.folders, fileId) ?? metaFromNodes(tree.files, fileId));
    if (meta) return meta;
  }
  return null;
}

export function fileToNode(file: FileResponse): DriveNode {
  return {
    id: file.id,
    mimeType: file.mimeType,
    modifiedTime: file.modifiedTime,
    name: file.name,
    parentId: file.parentFolderId,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}

export function injectNewFileIntoCaches(qc: QueryClient, file: FileResponse): void {
  const node = fileToNode(file);

  const parentKey = file.parentFolderId ?? file.driveId;

  if (parentKey) {
    qc.setQueryData<DriveNode[] | undefined>(wikiKeys.folderFiles(parentKey), (prev) => {
      if (!prev) return prev;
      return upsertConfirmedNode(prev, node);
    });
  }

  if (file.driveId) {
    const treeNode: DriveNode =
      node.parentId === file.driveId ? { ...node, parentId: undefined } : node;
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(file.driveId), (prev) => {
      if (!prev) return prev;
      if (file.mimeType === MIME_FOLDER) {
        const folders = upsertConfirmedNode(prev.folders, treeNode);
        return folders === prev.folders ? prev : { ...prev, folders };
      }
      const files = upsertConfirmedNode(prev.files, treeNode);
      return files === prev.files ? prev : { ...prev, files };
    });
  }
}

export function optimisticMoveFile(
  qc: QueryClient,
  node: DriveNode,
  destination: { parentFolderId: string; driveId: string },
  source: { parentFolderId: string | undefined; driveId: string | undefined },
): (() => void) & { commit: () => void } {
  const session = captureMutationSession(qc);
  const patches: MoveCachePatch[] = [];
  const sourceParent = source.parentFolderId ?? source.driveId;
  const sourceTree = source.driveId
    ? qc.getQueryData<DriveTree>(wikiKeys.driveTree(source.driveId))
    : undefined;
  const movingNode =
    sourceTree?.folders.find((item) => item.id === node.id) ??
    sourceTree?.files.find((item) => item.id === node.id) ??
    (sourceParent
      ? qc
          .getQueryData<DriveNode[]>(wikiKeys.folderFiles(sourceParent))
          ?.find((item) => item.id === node.id)
      : undefined) ??
    node;
  const moved = { ...movingNode, parentId: destination.parentFolderId };
  const treeNode =
    destination.parentFolderId === destination.driveId ? { ...moved, parentId: undefined } : moved;
  const treeKind = movingNode.mimeType === MIME_FOLDER ? "folders" : "files";
  if (!session.aborted) {
    if (sourceParent && sourceParent !== destination.parentFolderId) {
      patchMoveCache(
        qc,
        wikiKeys.folderFiles(sourceParent),
        "listing",
        node.id,
        undefined,
        patches,
      );
    }
    patchMoveCache(
      qc,
      wikiKeys.folderFiles(destination.parentFolderId),
      "listing",
      node.id,
      moved,
      patches,
    );
    if (source.driveId && source.driveId !== destination.driveId) {
      patchMoveCache(qc, wikiKeys.driveTree(source.driveId), treeKind, node.id, undefined, patches);
    }
    patchMoveCache(
      qc,
      wikiKeys.driveTree(destination.driveId),
      treeKind,
      node.id,
      treeNode,
      patches,
    );
  }
  const releaseCache = qc.getQueryCache().subscribe((event) => {
    for (const patch of patches) {
      if (patch.query !== event.query || !patch.owned) continue;
      if (
        event.type === "removed" ||
        (event.type === "updated" && event.action.type === "success" && !event.action.manual)
      ) {
        patch.owned = false;
      } else if (event.type === "updated" && event.action.type === "success") {
        const data = qc.getQueryData<DriveNode[] | DriveTree>(patch.queryKey);
        if (
          !data ||
          data === patch.data ||
          !ownsMoveNode(cacheNodes(data, patch.kind), node.id, patch.expected)
        )
          patch.owned = false;
        patch.data = data;
      }
    }
  });
  let released = false;
  const commit = () => {
    if (released) return;
    released = true;
    releaseCache();
    session.removeEventListener("abort", commit);
  };
  if (session.aborted) commit();
  else session.addEventListener("abort", commit, { once: true });
  const rollback = () => {
    if (released) return;
    if (!session.aborted) {
      for (const patch of patches) restoreMoveCache(qc, node.id, patch);
    }
    commit();
  };
  return Object.assign(rollback, { commit });
}

interface IndexedNode {
  index: number;
  node: DriveNode;
}

interface MoveCachePatch {
  before: IndexedNode[];
  data: DriveNode[] | DriveTree | undefined;
  expected: DriveNode[];
  kind: "files" | "folders" | "listing";
  owned: boolean;
  query: object;
  queryKey: QueryKey;
}

function patchMoveCache(
  qc: QueryClient,
  queryKey: QueryKey,
  kind: MoveCachePatch["kind"],
  fileId: string,
  moved: DriveNode | undefined,
  patches: MoveCachePatch[],
): void {
  // Reads are cancellable independently of an already admitted move action.
  void qc.cancelQueries({ exact: true, queryKey }).catch((error: unknown) => {
    console.warn("Cache read cancellation failed", error instanceof Error ? "Error" : typeof error);
  });
  const query = qc.getQueryCache().find({ exact: true, queryKey });
  const previous = qc.getQueryData<DriveNode[] | DriveTree>(queryKey);
  if (!previous || !query) return;
  const nodes = cacheNodes(previous, kind);
  const { before, movedIndex, next } = relocateNodes(nodes, fileId, moved);
  if (!moved && before.length === 0) return;
  const data = qc.setQueryData<DriveNode[] | DriveTree>(
    queryKey,
    cacheWithNodes(previous, kind, next),
  );
  const written = data ? cacheNodes(data, kind) : undefined;
  const expected = movedIndex === undefined ? undefined : written?.[movedIndex];
  patches.push({
    before,
    data,
    expected: expected ? [expected] : [],
    kind,
    owned: true,
    query,
    queryKey,
  });
}

function relocateNodes(nodes: DriveNode[], fileId: string, moved: DriveNode | undefined) {
  const before: IndexedNode[] = [];
  const next: DriveNode[] = [];
  let movedIndex: number | undefined;
  for (const [index, item] of nodes.entries()) {
    if (item.id !== fileId) next.push(item);
    else {
      before.push({ index, node: item });
      if (moved && movedIndex === undefined) {
        movedIndex = next.length;
        next.push(moved);
      }
    }
  }
  if (moved && movedIndex === undefined) {
    movedIndex = next.length;
    next.push(moved);
  }
  return { before, movedIndex, next };
}

function restoreMoveCache(qc: QueryClient, fileId: string, patch: MoveCachePatch): void {
  if (
    !patch.owned ||
    qc.getQueryCache().find({ exact: true, queryKey: patch.queryKey }) !== patch.query
  )
    return;
  const current = qc.getQueryData<DriveNode[] | DriveTree>(patch.queryKey);
  if (!current) return;
  const nodes = cacheNodes(current, patch.kind);
  if (!ownsMoveNode(nodes, fileId, patch.expected)) return;
  const restored = [...removeNode(nodes, fileId)];
  for (const { index, node } of patch.before)
    restored.splice(Math.min(index, restored.length), 0, node);
  qc.setQueryData(patch.queryKey, cacheWithNodes(current, patch.kind, restored));
}

function ownsMoveNode(nodes: DriveNode[], fileId: string, expected: DriveNode[]): boolean {
  const matches = nodes.filter((item) => item.id === fileId);
  return (
    matches.length === expected.length && matches.every((item, index) => item === expected[index])
  );
}

function isNodeArray(data: DriveNode[] | DriveTree): data is DriveNode[] {
  return Array.isArray(data);
}

function cacheNodes(data: DriveNode[] | DriveTree, kind: MoveCachePatch["kind"]): DriveNode[] {
  return isNodeArray(data) ? data : data[kind === "listing" ? "files" : kind];
}

function cacheWithNodes(
  data: DriveNode[] | DriveTree,
  kind: MoveCachePatch["kind"],
  nodes: DriveNode[],
): DriveNode[] | DriveTree {
  return isNodeArray(data) ? nodes : { ...data, [kind]: nodes };
}

export function removeFileFromCaches(
  qc: QueryClient,
  fileId: string,
  parentFolderId?: string,
  driveId?: string,
  options: { removeDriveTree?: boolean; removeFileQuery?: boolean } = {},
): void {
  const parentKey = parentFolderId ?? driveId;
  if (parentKey) {
    qc.setQueryData<DriveNode[] | undefined>(wikiKeys.folderFiles(parentKey), (prev) => {
      if (!prev) return prev;
      return removeNode(prev, fileId);
    });
  }
  if (driveId && options.removeDriveTree !== false) {
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(driveId), (prev) => {
      if (!prev) return prev;
      const files = removeNode(prev.files, fileId);
      const folders = removeNode(prev.folders, fileId);
      if (files === prev.files && folders === prev.folders) return prev;
      return {
        ...prev,
        files,
        folders,
      };
    });
  }

  if (options.removeFileQuery !== false) {
    qc.removeQueries({ queryKey: wikiKeys.file(fileId) });
  }
}

function upsertConfirmedNode(nodes: DriveNode[], node: DriveNode): DriveNode[] {
  const index = nodes.findIndex((item) => item.id === node.id);
  if (index === -1) return [...nodes, node];
  const existing = nodes[index];
  if (!existing) return nodes;
  const duplicate = nodes.some((item, itemIndex) => itemIndex > index && item.id === node.id);
  // FileResponse confirms these fields only; it carries no shortcut metadata.
  if (
    !duplicate &&
    existing.name === node.name &&
    existing.parentId === node.parentId &&
    existing.mimeType === node.mimeType &&
    existing.modifiedTime === node.modifiedTime
  )
    return nodes;
  const next = duplicate
    ? nodes.filter((item, itemIndex) => itemIndex === index || item.id !== node.id)
    : [...nodes];
  next[index] = {
    ...existing,
    mimeType: node.mimeType,
    modifiedTime: node.modifiedTime,
    name: node.name,
    parentId: node.parentId,
  };
  return next;
}

function removeNode(nodes: DriveNode[], fileId: string): DriveNode[] {
  const first = nodes.findIndex((node) => node.id === fileId);
  if (first === -1) return nodes;
  const remaining = nodes.slice(0, first);
  for (let i = first + 1; i < nodes.length; i += 1) {
    const node = nodes[i];
    if (node && node.id !== fileId) remaining.push(node);
  }
  return remaining;
}

function metaFromNodes(
  nodes: readonly DriveNode[] | undefined,
  fileId: string,
): CachedNodeMeta | null {
  if (!nodes) return null;
  const cached = NODE_META_CACHE.get(nodes);
  if (cached?.fileId === fileId) return cached.meta;
  const meta = lookupNodeMeta(nodes, fileId);
  NODE_META_CACHE.set(nodes, { fileId, meta });
  return meta;
}

function lookupNodeMeta(nodes: readonly DriveNode[], fileId: string): CachedNodeMeta | null {
  for (const node of nodes) {
    if (node.id === fileId && node.mimeType !== MIME_SHORTCUT) {
      return {
        mimeType: node.mimeType,
        ...(!(node.modifiedTime === undefined) && { modifiedTime: node.modifiedTime }),
        name: node.name,
      };
    }
    if (
      node.mimeType === MIME_SHORTCUT &&
      node.shortcutTargetId === fileId &&
      node.shortcutTargetMimeType
    ) {
      return {
        mimeType: node.shortcutTargetMimeType,
        name: node.name,
      };
    }
  }
  return null;
}
