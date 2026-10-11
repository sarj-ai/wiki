import type { QueryClient } from "@tanstack/react-query";

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
      if (prev.some((n) => n.id === node.id)) return prev;
      return [...prev, node];
    });
  }

  if (file.driveId) {
    const treeNode: DriveNode =
      node.parentId === file.driveId ? { ...node, parentId: undefined } : node;
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(file.driveId), (prev) => {
      if (!prev) return prev;
      if (file.mimeType === MIME_FOLDER) {
        if (prev.folders.some((folder) => folder.id === treeNode.id)) return prev;
        return { ...prev, folders: [...prev.folders, treeNode] };
      }
      const otherFiles = prev.files.filter((existing) => existing.id !== treeNode.id);
      return { ...prev, files: [...otherFiles, treeNode] };
    });
  }
}

export function optimisticMoveFile(
  qc: QueryClient,
  node: DriveNode,
  destination: { parentFolderId: string; driveId: string },
  source: { parentFolderId: string | undefined; driveId: string | undefined },
): () => void {
  removeFileFromCaches(qc, node.id, source.parentFolderId, source.driveId, {
    removeFileQuery: false,
  });
  injectNewFileIntoCaches(qc, {
    canEdit: false,
    driveId: destination.driveId,
    id: node.id,
    mimeType: node.mimeType,
    modifiedTime: node.modifiedTime,
    name: node.name,
    parentFolderId: destination.parentFolderId,
  });
  return () => {
    removeFileFromCaches(qc, node.id, destination.parentFolderId, destination.driveId, {
      removeFileQuery: false,
    });
    injectNewFileIntoCaches(qc, {
      canEdit: false,
      driveId: source.driveId,
      id: node.id,
      mimeType: node.mimeType,
      modifiedTime: node.modifiedTime,
      name: node.name,
      parentFolderId: source.parentFolderId,
    });
  };
}

export function removeFileFromCaches(
  qc: QueryClient,
  fileId: string,
  parentFolderId?: string,
  driveId?: string,
  options: { removeFileQuery?: boolean } = {},
): void {
  const parentKey = parentFolderId ?? driveId;
  if (parentKey) {
    qc.setQueryData<DriveNode[] | undefined>(wikiKeys.folderFiles(parentKey), (prev) => {
      if (!prev) return prev;
      return removeNode(prev, fileId);
    });
  }
  if (driveId) {
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
        ...(node.modifiedTime === undefined ? {} : { modifiedTime: node.modifiedTime }),
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
