"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  bulkSetSortOrdersAction,
  copyFileAction,
  createFileAction,
  deleteFileAction,
  moveFileAction,
  refreshDriveAction,
  renameFileAction,
  setDocIconAction,
  setFolderIconAction,
  setPreferencesAction,
} from "@/app/actions/wiki-actions";
import { wikiKeys } from "@/hooks/use-wiki-tree";
import { runAction } from "@/lib/action-client";
import type { CreateFileInput, PreferencesInput } from "@/lib/wiki-api";
import { injectNewFileIntoCaches, removeFileFromCaches } from "@/lib/wiki-cache";
import type { DocIcon, DriveTree, FolderIcon, SortOrder } from "@/types/wiki";

export function useCreateFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateFileInput) =>
      await runAction(async () => await createFileAction(input)),
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRenameFile() {
  const invalidate = useInvalidateTrees();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ fileId, name }: { fileId: string; name: string }) =>
      runAction(async () => await renameFileAction(fileId, name)),
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
      const result = await runAction(async () => await moveFileAction(fileId, newParentFolderId));
      return { result, sourceDriveId, sourceParentFolderId };
    },
    onError: (_err, _vars, context: { previousSortOrders?: SortOrder[] } | undefined) => {
      if (context?.previousSortOrders) {
        qc.setQueryData(wikiKeys.sortOrders, context.previousSortOrders);
      }
    },
    onMutate: ({ fileId }) => {
      const previousSortOrders = dropSortOrderLocally(qc, fileId);
      return previousSortOrders === undefined ? {} : { previousSortOrders };
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
    mutationFn: async ({ fileId, name }: { fileId: string; name: string }) =>
      runAction(async () => await copyFileAction(fileId, name)),
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRefreshDrive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (driveId: string) =>
      await runAction(async () => await refreshDriveAction(driveId)),
    onSuccess: (tree: DriveTree) => {
      qc.setQueryData<DriveTree>(wikiKeys.driveTree(tree.driveId), tree);
      void qc.invalidateQueries({ queryKey: wikiKeys.folderFilesPrefix });
    },
  });
}

export function useDeleteFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      driveId: _driveId,
      fileId,
      parentFolderId: _parentFolderId,
    }: {
      fileId: string;
      driveId: string | undefined;
      parentFolderId: string | undefined;
    }) => runAction(async () => await deleteFileAction(fileId)),
    onSuccess: (_void, { driveId, fileId, parentFolderId }) => {
      removeFileFromCaches(qc, fileId, parentFolderId, driveId);
    },
  });
}

export function useBulkSetSortOrders() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (items: SortOrder[]) =>
      await runAction(async () => await bulkSetSortOrdersAction(items)),

    onError: (_err, _items, context: { previous: SortOrder[] } | undefined) => {
      if (context) {
        qc.setQueryData(wikiKeys.sortOrders, context.previous);
      }
    },
    onMutate: (items) => {
      const previous = qc.getQueryData<SortOrder[]>(wikiKeys.sortOrders) ?? [];
      const byId = new Map(previous.map((s) => [s.driveId, s] as const));
      for (const item of items) byId.set(item.driveId, item);
      qc.setQueryData<SortOrder[]>(wikiKeys.sortOrders, byId.values().toArray());
      return { previous };
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: wikiKeys.sortOrders });
    },
  });
}

export function useSetPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: PreferencesInput) =>
      await runAction(async () => await setPreferencesAction(input)),
    onSuccess: (prefs) => {
      qc.setQueryData(wikiKeys.preferences, prefs);
    },
  });
}

export function useSetDocIcon() {
  return useIconMutation<DocIcon, { driveId: string; icon: null | string }>({
    buildItem: ({ driveId, icon }, existing) => ({
      coverUrl: existing?.coverUrl,
      driveId,
      icon: icon ?? undefined,
    }),
    matches: (item, { driveId }) => item.driveId === driveId,
    mutationFn: async ({ driveId, icon }) =>
      await runAction(async () => await setDocIconAction(driveId, { icon })),

    queryKey: wikiKeys.docIcons,
  });
}

export function useSetFolderIcon() {
  return useIconMutation<FolderIcon, { folderDriveId: string; icon: null | string }>({
    buildItem: ({ folderDriveId, icon }) => ({ folderDriveId, icon: icon ?? undefined }),
    matches: (item, { folderDriveId }) => item.folderDriveId === folderDriveId,
    mutationFn: async ({ folderDriveId, icon }) =>
      await runAction(async () => await setFolderIconAction(folderDriveId, icon)),
    queryKey: wikiKeys.folderIcons,
  });
}

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
  return (driveIds: (null | string | undefined)[]) => {
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
    onError: (_err, _vars, context: { previous?: TItem[] } | undefined) => {
      if (context?.previous) qc.setQueryData(config.queryKey, context.previous);
    },
    onMutate: (vars: TVars) => {
      const previous = qc.getQueryData<TItem[]>(config.queryKey);
      if (previous) {
        const existing = previous.find((item) => config.matches(item, vars));
        qc.setQueryData<TItem[]>(config.queryKey, [
          ...previous.filter((item) => !config.matches(item, vars)),
          config.buildItem(vars, existing),
        ]);
      }
      return previous === undefined ? {} : { previous };
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: config.queryKey });
    },
  });
}
