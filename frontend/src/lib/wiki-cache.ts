import { type QueryClient } from "@tanstack/react-query";

import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";

import { wikiKeys } from "@/lib/wiki-keys";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

export interface CachedNodeMeta {
  name: string;
  mimeType: string;
}

export function findCachedNodeMeta(qc: QueryClient, fileId: string): CachedNodeMeta | null {
  for (const [, nodes] of qc.getQueriesData<DriveNode[]>({
    queryKey: wikiKeys.folderFilesPrefix,
  })) {
    const meta = metaFromNodes(nodes ?? [], fileId);
    if (meta) return meta;
  }
  for (const [, tree] of qc.getQueriesData<DriveTree>({ queryKey: wikiKeys.driveTreePrefix })) {
    const meta = tree && metaFromNodes([...tree.folders, ...tree.files], fileId);
    if (meta) return meta;
  }
  return null;
}

export function fileToNode(file: FileResponse): DriveNode {
  return {
    id: file.id,
    name: file.name,
    mimeType: file.mimeType,
    parentId: file.parentFolderId,
    modifiedTime: file.modifiedTime,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutInaccessible: false,
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

  if (file.driveId && file.mimeType === MIME_FOLDER) {
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(file.driveId), (prev) => {
      if (!prev) return prev;
      if (prev.folders.some((f) => f.id === node.id)) return prev;
      return { ...prev, folders: [...prev.folders, node] };
    });
  }
  if (file.driveId && file.mimeType !== MIME_FOLDER) {
    const treeNode: DriveNode =
      node.parentId === file.driveId ? { ...node, parentId: undefined } : node;
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(file.driveId), (prev) => {
      if (!prev) return prev;
      const others = prev.files.filter((f) => f.id !== treeNode.id);
      return { ...prev, files: [...others, treeNode] };
    });
  }
}

export function optimisticMoveFile(
  qc: QueryClient,
  node: DriveNode,
  destination: { parentFolderId: string; driveId: string },
  source: { parentFolderId: string | undefined; driveId: string | undefined },
): () => void {
  removeFileFromCaches(qc, node.id, source.parentFolderId, source.driveId);
  injectNewFileIntoCaches(qc, {
    id: node.id,
    name: node.name,
    mimeType: node.mimeType,
    parentFolderId: destination.parentFolderId,
    driveId: destination.driveId,
    modifiedTime: node.modifiedTime,
  });
  return () => {
    removeFileFromCaches(qc, node.id, destination.parentFolderId, destination.driveId);
    injectNewFileIntoCaches(qc, {
      id: node.id,
      name: node.name,
      mimeType: node.mimeType,
      parentFolderId: source.parentFolderId,
      driveId: source.driveId,
      modifiedTime: node.modifiedTime,
    });
  };
}

export function removeFileFromCaches(
  qc: QueryClient,
  fileId: string,
  parentFolderId?: string,
  driveId?: string,
): void {
  const parentKey = parentFolderId ?? driveId;
  if (parentKey) {
    qc.setQueryData<DriveNode[] | undefined>(wikiKeys.folderFiles(parentKey), (prev) => {
      if (!prev) return prev;
      return prev.filter((n) => n.id !== fileId);
    });
  }
  if (driveId) {
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(driveId), (prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        folders: prev.folders.filter((f) => f.id !== fileId),
        files: prev.files.filter((f) => f.id !== fileId),
      };
    });
  }

  qc.removeQueries({ queryKey: wikiKeys.file(fileId) });
}

function metaFromNodes(nodes: DriveNode[], fileId: string): CachedNodeMeta | null {
  for (const node of nodes) {
    if (node.id === fileId && node.mimeType !== MIME_SHORTCUT) {
      return { name: node.name, mimeType: node.mimeType };
    }
    if (
      node.mimeType === MIME_SHORTCUT &&
      node.shortcutTargetId === fileId &&
      node.shortcutTargetMimeType
    ) {
      return { name: node.name, mimeType: node.shortcutTargetMimeType };
    }
  }
  return null;
}
