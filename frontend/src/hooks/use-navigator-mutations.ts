"use client";

import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { navigatorApi, type CreateFileInput, type PreferencesInput } from "@/lib/navigator-api";
import { navigatorKeys } from "@/hooks/use-navigator-tree";
import type { DriveNode, DriveTree, FileResponse, SortOrder } from "@/types/navigator";
import { MIME_FOLDER } from "@/types/navigator";

// Turns a fresh FileResponse into a DriveNode so it can be spliced
// straight into the sidebar caches without waiting for a refetch.
function fileToNode(file: FileResponse): DriveNode {
  return {
    id: file.id,
    name: file.name,
    mimeType: file.mimeType,
    parentId: file.parentFolderId ?? null,
    modifiedTime: file.modifiedTime ?? null,
  };
}

// Inject a brand-new file/folder into every cache the sidebar reads so
// the new row paints instantly on the optimistic path. The subsequent
// refetch (kicked off by invalidateQueries) will reconcile any drift —
// this is purely UX smoothing, not source-of-truth.
function injectNewFileIntoCaches(qc: QueryClient, file: FileResponse): void {
  const node = fileToNode(file);
  const parentId = file.parentFolderId;

  // 1. Folder-files cache: append to the parent folder's listing so an
  // already-expanded folder paints the new row on the next render.
  if (parentId) {
    qc.setQueryData<DriveNode[] | undefined>(navigatorKeys.folderFiles(parentId), (prev) => {
      if (!prev) return prev;
      if (prev.some((n) => n.id === node.id)) return prev;
      return [...prev, node];
    });
  }

  // 2. Drive-tree cache: only matters for new folders — the tree walker
  // only holds folders, so a new file changes nothing there. A new
  // folder goes into `folders` with its parent link so it shows up as
  // a child without waiting for the background walk.
  if (file.driveId && file.mimeType === MIME_FOLDER) {
    qc.setQueryData<DriveTree | undefined>(navigatorKeys.driveTree(file.driveId), (prev) => {
      if (!prev) return prev;
      if (prev.folders.some((f) => f.id === node.id)) return prev;
      return { ...prev, folders: [...prev.folders, node] };
    });
  }
}

// Writes invalidate the drive-tree cache for whichever drive the
// affected file belongs to (or both source and destination on a
// cross-drive move). The navigator-api backend already drops its own
// cache on writes; this React Query invalidation keeps the client
// state coherent so the sidebar re-renders without a manual refetch.
function useInvalidateTrees() {
  const qc = useQueryClient();
  return (driveIds: (string | null | undefined)[]) => {
    for (const id of driveIds) {
      if (id) qc.invalidateQueries({ queryKey: navigatorKeys.driveTree(id) });
    }
    // Folder-file listings inside moved folders may now be stale — drop
    // them all so the next expand refetches. Cheap vs scoped invalidation.
    qc.invalidateQueries({ queryKey: ["navigator", "folder-files"] });
  };
}

export function useCreateFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateFileInput) => navigatorApi.createFile(input),
    onSuccess: (file) => {
      // Splice the new file into the local caches and STOP — do NOT
      // invalidate folder-files here. Drive's file index lags a few
      // seconds behind files.create/copy, so an immediate refetch
      // would return a listing that's still missing the new row and
      // overwrite the optimistic insert, causing it to flash in and
      // out of the sidebar. The cache mutation uses the authoritative
      // server response as its source of truth, so skipping the
      // refetch is safe.
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRenameFile() {
  const invalidate = useInvalidateTrees();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fileId, name }: { fileId: string; name: string }) =>
      navigatorApi.renameFile(fileId, name),
    onSuccess: (file) => {
      invalidate([file.driveId]);
      qc.invalidateQueries({ queryKey: ["navigator", "file", file.id] });
    },
  });
}

export function useMoveFile() {
  const invalidate = useInvalidateTrees();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      fileId,
      newParentFolderId,
      sourceDriveId,
    }: {
      fileId: string;
      newParentFolderId: string;
      // Caller passes the source drive so we can invalidate it even if
      // the post-move response only carries the destination drive id.
      sourceDriveId: string | null;
    }) => {
      const result = await navigatorApi.moveFile(fileId, newParentFolderId);
      return { result, sourceDriveId };
    },
    onSuccess: ({ result, sourceDriveId }) => {
      invalidate([sourceDriveId, result.driveId]);
      qc.invalidateQueries({ queryKey: ["navigator", "file", result.id] });
    },
  });
}

export function useCopyFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fileId, name }: { fileId: string; name: string }) =>
      navigatorApi.copyFile(fileId, name),
    onSuccess: (file) => {
      // Same no-invalidate rationale as useCreateFile: Drive's listing
      // lags copy by a couple of seconds, so an eager refetch races
      // the optimistic insert and the new row disappears before Drive
      // catches up.
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useDeleteFile() {
  const invalidate = useInvalidateTrees();
  return useMutation({
    mutationFn: ({ fileId, driveId: _driveId }: { fileId: string; driveId: string | null }) =>
      navigatorApi.deleteFile(fileId),
    onSuccess: (_void, { driveId }) => {
      invalidate([driveId]);
    },
  });
}

export function useBulkSetSortOrders() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: SortOrder[]) => navigatorApi.bulkSetSortOrders(items),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: navigatorKeys.sortOrders });
    },
  });
}

export function useSetPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PreferencesInput) => navigatorApi.setPreferences(payload),
    onSuccess: (prefs) => {
      // Overwrite the preferences cache so the sidebar picks up the
      // new visibility flags immediately without a refetch round-trip.
      qc.setQueryData(navigatorKeys.preferences, prefs);
    },
  });
}

export function useSetDocIcon() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ driveId, icon }: { driveId: string; icon: string | null }) =>
      navigatorApi.setDocIcon(driveId, { icon }),
    onSuccess: () => {
      // Doc icons are global — one refetch updates the map for the
      // whole sidebar. Simpler than patching the cached array by hand.
      qc.invalidateQueries({ queryKey: navigatorKeys.docIcons });
    },
  });
}

export function useSetFolderIcon() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ folderDriveId, icon }: { folderDriveId: string; icon: string | null }) =>
      navigatorApi.setFolderIcon(folderDriveId, icon),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: navigatorKeys.folderIcons });
    },
  });
}
