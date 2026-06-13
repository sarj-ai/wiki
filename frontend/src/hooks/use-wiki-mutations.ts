"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { DocIcon, DriveTree, FolderIcon, SortOrder } from "@/types/wiki";

import { wikiKeys } from "@/hooks/use-wiki-tree";
import { wikiApi } from "@/lib/wiki-api";
import { injectNewFileIntoCaches, removeFileFromCaches } from "@/lib/wiki-cache";

function dropSortOrderLocally(
  qc: ReturnType<typeof useQueryClient>,
  fileId: string,
): SortOrder[] | undefined {
  const previous = qc.getQueryData<SortOrder[]>(wikiKeys.sortOrders);
  if (!previous) return undefined;
  qc.setQueryData<SortOrder[]>(
    wikiKeys.sortOrders,
    previous.filter((s) => s.driveId !== fileId),
  );
  return previous;
}

function useInvalidateTrees() {
  const qc = useQueryClient();
  return (driveIds: (string | null | undefined)[]) => {
    for (const id of driveIds) {
      if (id) void qc.invalidateQueries({ queryKey: wikiKeys.driveTree(id) });
    }

    void qc.invalidateQueries({ queryKey: wikiKeys.folderFilesPrefix });
  };
}

function useIconMutation<TItem, TVars>(config: {
  queryKey: readonly unknown[];
  mutationFn: (vars: TVars) => Promise<unknown>;
  matches: (item: TItem, vars: TVars) => boolean;
  buildItem: (vars: TVars, existing: TItem | undefined) => TItem;
}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: config.mutationFn,
    onMutate: (vars: TVars) => {
      const previous = qc.getQueryData<TItem[]>(config.queryKey);
      if (previous) {
        const existing = previous.find((item) => config.matches(item, vars));
        qc.setQueryData<TItem[]>(config.queryKey, [
          ...previous.filter((item) => !config.matches(item, vars)),
          config.buildItem(vars, existing),
        ]);
      }
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) qc.setQueryData(config.queryKey, context.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: config.queryKey });
    },
  });
}

export function useCreateFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: wikiApi.createFile,
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRenameFile() {
  const invalidate = useInvalidateTrees();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fileId, name }: { fileId: string; name: string }) =>
      wikiApi.renameFile(fileId, name),
    onSuccess: (file) => {
      invalidate([file.driveId]);
      void qc.invalidateQueries({ queryKey: wikiKeys.file(file.id) });
    },
  });
}

export function useMoveFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      fileId,
      newParentFolderId,
      sourceDriveId,
      sourceParentFolderId,
    }: {
      fileId: string;
      newParentFolderId: string;

      sourceDriveId: string | undefined;
      sourceParentFolderId: string | undefined;
    }) => {
      const result = await wikiApi.moveFile(fileId, newParentFolderId);
      return { result, sourceDriveId, sourceParentFolderId };
    },
    onMutate: ({ fileId }) => {
      const previousSortOrders = dropSortOrderLocally(qc, fileId);
      return { previousSortOrders };
    },
    onError: (_err, _vars, context) => {
      if (context?.previousSortOrders) {
        qc.setQueryData(wikiKeys.sortOrders, context.previousSortOrders);
      }
    },
    onSuccess: ({ result, sourceDriveId, sourceParentFolderId }) => {
      removeFileFromCaches(qc, result.id, sourceParentFolderId, sourceDriveId);
      injectNewFileIntoCaches(qc, result);

      void qc.invalidateQueries({ queryKey: wikiKeys.file(result.id) });
      void qc.invalidateQueries({ queryKey: wikiKeys.sortOrders });
    },
  });
}

export function useCopyFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fileId, name }: { fileId: string; name: string }) =>
      wikiApi.copyFile(fileId, name),
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRefreshDrive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: wikiApi.refreshDriveFolders,
    onSuccess: (tree: DriveTree) => {
      qc.setQueryData<DriveTree>(wikiKeys.driveTree(tree.driveId), tree);
      void qc.invalidateQueries({ queryKey: wikiKeys.folderFilesPrefix });
    },
  });
}

export function useDeleteFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      fileId,
      driveId: _driveId,
      parentFolderId: _parentFolderId,
    }: {
      fileId: string;
      driveId: string | undefined;
      parentFolderId: string | undefined;
    }) => wikiApi.deleteFile(fileId),
    onSuccess: (_void, { fileId, driveId, parentFolderId }) => {
      removeFileFromCaches(qc, fileId, parentFolderId, driveId);
    },
  });
}

export function useBulkSetSortOrders() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: wikiApi.bulkSetSortOrders,

    onMutate: (items) => {
      const previous = qc.getQueryData<SortOrder[]>(wikiKeys.sortOrders) ?? [];
      const byId = new Map(previous.map((s) => [s.driveId, s] as const));
      for (const item of items) byId.set(item.driveId, item);
      qc.setQueryData<SortOrder[]>(wikiKeys.sortOrders, Array.from(byId.values()));
      return { previous };
    },
    onError: (_err, _items, context) => {
      if (context) {
        qc.setQueryData(wikiKeys.sortOrders, context.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: wikiKeys.sortOrders });
    },
  });
}

export function useSetPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: wikiApi.setPreferences,
    onSuccess: (prefs) => {
      qc.setQueryData(wikiKeys.preferences, prefs);
    },
  });
}

export function useSetDocIcon() {
  return useIconMutation<DocIcon, { driveId: string; icon: string | null }>({
    queryKey: wikiKeys.docIcons,
    mutationFn: ({ driveId, icon }) => wikiApi.setDocIcon(driveId, { icon }),
    matches: (item, { driveId }) => item.driveId === driveId,

    buildItem: ({ driveId, icon }, existing) => ({
      driveId,
      icon: icon ?? undefined,
      coverUrl: existing?.coverUrl,
    }),
  });
}

export function useSetFolderIcon() {
  return useIconMutation<FolderIcon, { folderDriveId: string; icon: string | null }>({
    queryKey: wikiKeys.folderIcons,
    mutationFn: ({ folderDriveId, icon }) => wikiApi.setFolderIcon(folderDriveId, icon),
    matches: (item, { folderDriveId }) => item.folderDriveId === folderDriveId,
    buildItem: ({ folderDriveId, icon }) => ({ folderDriveId, icon: icon ?? undefined }),
  });
}
