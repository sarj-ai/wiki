import { type QueryClient } from "@tanstack/react-query";

import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";

import { wikiKeys } from "@/hooks/use-wiki-tree";
import { MIME_FOLDER } from "@/types/wiki";

/**
 * Pure cache helpers for the optimistic insert/remove paths used by
 * the file-mutation hooks. Lifted out of ``use-wiki-mutations``
 * so they're trivially unit-testable against a real ``QueryClient``
 * — no React, no hooks, no DOM.
 *
 * Optimistic edits are intentionally one-way: the server response
 * is the authoritative shape, and the helpers splice exactly what
 * the sidebar reads (folder-file listings + drive trees). The
 * subsequent ``invalidateQueries`` in the mutation hooks reconciles
 * any drift; this module just smooths the paint.
 */

/**
 * Project a fresh ``FileResponse`` from the server into the
 * ``DriveNode`` shape the sidebar caches store.
 */
export function fileToNode(file: FileResponse): DriveNode {
  return {
    id: file.id,
    name: file.name,
    mimeType: file.mimeType,
    parentId: file.parentFolderId ?? null,
    modifiedTime: file.modifiedTime ?? null,
  };
}

/**
 * Splice a brand-new file/folder into every sidebar cache so the row
 * paints on the next render. New folders also land in the drive-tree
 * cache so children mount without waiting for the background walk.
 */
export function injectNewFileIntoCaches(qc: QueryClient, file: FileResponse): void {
  const node = fileToNode(file);
  // Root-level files live under ``folderFiles(driveId)`` — the drive
  // section treats ``parentFolderId === null`` as "this drive's root"
  // and reads via ``useFolderFiles(drive.id)``. Fall back to the drive
  // id so the optimistic insert lands in the same cache.
  const parentKey = file.parentFolderId ?? file.driveId;

  if (parentKey) {
    qc.setQueryData<DriveNode[] | undefined>(wikiKeys.folderFiles(parentKey), (prev) => {
      if (!prev) return prev;
      if (prev.some((n) => n.id === node.id)) return prev;
      return [...prev, node];
    });
  }

  // Drive-tree cache only holds folders; files are no-ops here. Adding
  // the new folder up front means an already-expanded ancestor paints
  // it without waiting for the BFS walk to refresh.
  if (file.driveId && file.mimeType === MIME_FOLDER) {
    qc.setQueryData<DriveTree | undefined>(wikiKeys.driveTree(file.driveId), (prev) => {
      if (!prev) return prev;
      if (prev.folders.some((f) => f.id === node.id)) return prev;
      return { ...prev, folders: [...prev.folders, node] };
    });
  }
}

/**
 * Optimistically reflect a move in the sidebar caches so the row
 * lands at its destination on the same frame the user drops, without
 * waiting for the Drive round-trip. The dragged ``DriveNode`` is
 * removed from its source caches and re-injected at the new parent;
 * a returned ``rollback`` reverses both edits if the mutation fails.
 *
 * We never get a fresh ``FileResponse`` until the server replies —
 * the optimistic insert synthesises one from the existing
 * ``DriveNode`` plus the destination coordinates, which is enough
 * for the sidebar render path. The eventual ``invalidateQueries`` in
 * ``useMoveFile.onSuccess`` reconciles any drift.
 */
export function optimisticMoveFile(
  qc: QueryClient,
  node: DriveNode,
  destination: { parentFolderId: string; driveId: string },
  source: { parentFolderId: string | null; driveId: string | null },
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

/**
 * Strip a deleted node from every sidebar cache so the row vanishes
 * the moment the mutation settles. Same rationale as the inject path
 * — Drive's listing index lags trash, so an eager refetch would
 * return the file still present and the row would flash back.
 */
export function removeFileFromCaches(
  qc: QueryClient,
  fileId: string,
  parentFolderId: string | null,
  driveId: string | null,
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
      return { ...prev, folders: prev.folders.filter((f) => f.id !== fileId) };
    });
  }
  // Drop any stale per-file metadata so a re-navigation doesn't show
  // cached info for a file that's now in trash.
  qc.removeQueries({ queryKey: ["wiki", "file", fileId] });
}
