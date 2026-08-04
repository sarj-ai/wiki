import type { QueryClient } from "@tanstack/react-query";

import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";
import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

export interface CachedNodeMeta {
  mimeType: string;
  name: string;
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
      return prev.filter((n) => n.id !== fileId);
    });
  }
  if (driveId) {
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(driveId), (prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        files: prev.files.filter((f) => f.id !== fileId),
        folders: prev.folders.filter((f) => f.id !== fileId),
      };
    });
  }

  if (options.removeFileQuery !== false) {
    qc.removeQueries({ queryKey: wikiKeys.file(fileId) });
  }
}

function metaFromNodes(nodes: DriveNode[], fileId: string): CachedNodeMeta | null {
  for (const node of nodes) {
    if (node.id === fileId && node.mimeType !== MIME_SHORTCUT) {
      return { mimeType: node.mimeType, name: node.name };
    }
    if (
      node.mimeType === MIME_SHORTCUT &&
      node.shortcutTargetId === fileId &&
      node.shortcutTargetMimeType
    ) {
      return { mimeType: node.shortcutTargetMimeType, name: node.name };
    }
  }
  return null;
}
