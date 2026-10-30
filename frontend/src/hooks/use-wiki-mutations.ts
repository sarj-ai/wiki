"use client";

import { useQueryClient } from "@tanstack/react-query";

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
import { useSessionMutation } from "@/hooks/use-session-mutation";
import { wikiKeys } from "@/hooks/use-wiki-tree";
import { runAction } from "@/lib/action-client";
import { captureMutationSession } from "@/lib/mutation-session";
import {
  type OptimisticFieldsChange,
  type OptimisticListChange,
  updateOptimisticFields,
  updateOptimisticList,
} from "@/lib/optimistic-cache";
import type { CreateFileInput, PreferencesInput } from "@/lib/wiki-api";
import { injectNewFileIntoCaches, removeFileFromCaches } from "@/lib/wiki-cache";
import type {
  DocIcon,
  DriveTree,
  FileDetail,
  FolderIcon,
  Preferences,
  SortOrder,
} from "@/types/wiki";

interface ListMutationContext {
  change: OptimisticListChange | undefined;
}

interface PreferencesMutationContext {
  change: OptimisticFieldsChange<Preferences> | undefined;
}

export function useCreateFile() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (input: CreateFileInput, _context, session) =>
      await runAction(async () => await createFileAction(input), session),
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRenameFile() {
  const invalidate = useInvalidateTrees();
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async ({ fileId, name }: { fileId: string; name: string }, _context, session) =>
      runAction(async () => await renameFileAction(fileId, name), session),
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
      qc.setQueryData<FileDetail>(wikiKeys.file(file.id), (previous) =>
        previous ? { ...previous, modifiedTime: file.modifiedTime, name: file.name } : previous,
      );
      invalidate([file.driveId]);
      void qc.invalidateQueries({ queryKey: wikiKeys.file(file.id) });
    },
  });
}

export function useMoveFile() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (
      {
        fileId,
        newParentFolderId,
        sourceDriveId,
        sourceParentFolderId,
      }: {
        fileId: string;
        newParentFolderId: string;

        sourceDriveId: string | undefined;
        sourceParentFolderId: string | undefined;
      },
      _context,
      session,
    ) => {
      const result = await runAction(
        async () => await moveFileAction(fileId, newParentFolderId),
        session,
      );
      return { result, sourceDriveId, sourceParentFolderId };
    },
    onError: (_err, _vars, context: ListMutationContext | undefined) => {
      context?.change?.rollback();
    },
    onMutate: async ({ fileId }) => {
      const previous = qc.getQueryData<SortOrder[]>(wikiKeys.sortOrders);
      if (!previous?.some((item) => item.driveId === fileId)) return { change: undefined };
      const session = captureMutationSession(qc);
      await qc.cancelQueries({ exact: true, queryKey: wikiKeys.sortOrders });
      session.throwIfAborted();
      const change = updateOptimisticList<SortOrder>(
        qc,
        wikiKeys.sortOrders,
        session,
        new Map([[fileId, undefined]]),
        (item) => item.driveId,
      );
      return { change };
    },
    onSettled: (_data, _error, _vars, context) => {
      if (!context?.change || context.change.settle()) {
        void qc.invalidateQueries({ exact: true, queryKey: wikiKeys.sortOrders });
      }
    },
    onSuccess: ({ result, sourceDriveId, sourceParentFolderId }) => {
      removeFileFromCaches(qc, result.id, sourceParentFolderId, sourceDriveId, {
        removeDriveTree: sourceDriveId !== result.driveId,
        removeFileQuery: false,
      });
      injectNewFileIntoCaches(qc, result);

      void qc.invalidateQueries({ queryKey: wikiKeys.file(result.id) });
    },
  });
}

export function useCopyFile() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async ({ fileId, name }: { fileId: string; name: string }, _context, session) =>
      runAction(async () => await copyFileAction(fileId, name), session),
    onSuccess: (file) => {
      injectNewFileIntoCaches(qc, file);
    },
  });
}

export function useRefreshDrive() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (driveId: string, _context, session) =>
      await runAction(async () => await refreshDriveAction(driveId), session),
    onSuccess: (tree: DriveTree) => {
      const previous = qc.getQueryData<DriveTree>(wikiKeys.driveTree(tree.driveId));
      qc.setQueryData<DriveTree>(wikiKeys.driveTree(tree.driveId), tree);
      const unrelated = unrelatedFolderIds(qc, previous, tree);
      void qc.invalidateQueries({
        predicate: (query) => !unrelated.has(String(query.queryKey[2])),
        queryKey: wikiKeys.folderFilesPrefix,
      });
    },
  });
}

export function useDeleteFile() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (
      {
        driveId: _driveId,
        fileId,
        parentFolderId: _parentFolderId,
      }: {
        fileId: string;
        driveId: string | undefined;
        parentFolderId: string | undefined;
      },
      _context,
      session,
    ) => runAction(async () => await deleteFileAction(fileId), session),
    onSuccess: (_void, { driveId, fileId, parentFolderId }) => {
      removeFileFromCaches(qc, fileId, parentFolderId, driveId);
    },
  });
}

export function useBulkSetSortOrders() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (items: SortOrder[], _context, session) =>
      await runAction(async () => await bulkSetSortOrdersAction(items), session),

    onError: (_err, _items, context: ListMutationContext | undefined) => {
      context?.change?.rollback();
    },
    onMutate: async (items) => {
      const session = captureMutationSession(qc);
      await qc.cancelQueries({ exact: true, queryKey: wikiKeys.sortOrders });
      session.throwIfAborted();
      const change = updateOptimisticList<SortOrder>(
        qc,
        wikiKeys.sortOrders,
        session,
        new Map(items.map((item) => [item.driveId, () => item])),
        (item) => item.driveId,
      );
      return { change };
    },
    onSettled: (_data, _error, _vars, context) => {
      if (!context?.change || context.change.settle()) {
        void qc.invalidateQueries({ exact: true, queryKey: wikiKeys.sortOrders });
      }
    },
  });
}

export function useSetPreferences() {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (input: PreferencesInput, _context, session) =>
      await runAction(async () => await setPreferencesAction(input), session),
    onError: (_error, _input, context: PreferencesMutationContext | undefined) => {
      context?.change?.rollback();
    },
    onMutate: async (input) => {
      const session = captureMutationSession(qc);
      await qc.cancelQueries({ exact: true, queryKey: wikiKeys.preferences });
      session.throwIfAborted();
      return {
        change: updateOptimisticFields<Preferences>(qc, wikiKeys.preferences, session, input),
      };
    },
    onSettled: async (_data, _error, _input, context) => {
      if (context?.change?.settle()) {
        await qc.invalidateQueries({ exact: true, queryKey: wikiKeys.preferences });
      }
    },
    onSuccess: (prefs, _input, context) => {
      if (context.change) context.change.confirm(prefs);
      else qc.setQueryData(wikiKeys.preferences, prefs);
    },
    scope: { id: "wiki-preferences" },
  });
}

export function useSetDocIcon() {
  return useIconMutation<DocIcon, { driveId: string; icon: null | string }>({
    buildItem: ({ driveId, icon }, existing) => ({
      coverUrl: existing?.coverUrl,
      driveId,
      icon: icon ?? undefined,
    }),
    itemId: (item) => item.driveId,
    variableId: ({ driveId }) => driveId,
    mutationFn: async ({ driveId, icon }, session) =>
      await runAction(async () => await setDocIconAction(driveId, { icon }), session),

    queryKey: wikiKeys.docIcons,
  });
}

export function useSetFolderIcon() {
  return useIconMutation<FolderIcon, { folderDriveId: string; icon: null | string }>({
    buildItem: ({ folderDriveId, icon }) => ({ folderDriveId, icon: icon ?? undefined }),
    itemId: (item) => item.folderDriveId,
    variableId: ({ folderDriveId }) => folderDriveId,
    mutationFn: async ({ folderDriveId, icon }, session) =>
      await runAction(async () => await setFolderIconAction(folderDriveId, icon), session),
    queryKey: wikiKeys.folderIcons,
  });
}

function unrelatedFolderIds(
  qc: ReturnType<typeof useQueryClient>,
  previous: DriveTree | undefined,
  refreshed: DriveTree,
): Set<string> {
  const unrelated = new Set<string>();
  for (const [, tree] of qc.getQueriesData<DriveTree>({ queryKey: wikiKeys.driveTreePrefix })) {
    if (!tree || tree.driveId === refreshed.driveId) continue;
    unrelated.add(tree.driveId);
    for (const folder of tree.folders) unrelated.add(folder.id);
  }
  // Unknown listings still invalidate. Protect both old and new membership,
  // including shortcut targets shared with another cached Drive.
  unrelated.delete(refreshed.driveId);
  for (const tree of [previous, refreshed]) {
    if (!tree) continue;
    for (const folder of tree.folders) {
      unrelated.delete(folder.id);
      if (folder.shortcutTargetId) unrelated.delete(folder.shortcutTargetId);
    }
  }
  return unrelated;
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
  mutationFn: (vars: TVars, session: AbortSignal) => Promise<unknown>;
  itemId: (item: TItem) => string;
  variableId: (vars: TVars) => string;
  buildItem: (vars: TVars, existing: TItem | undefined) => TItem;
}) {
  const qc = useQueryClient();
  return useSessionMutation({
    mutationFn: async (vars: TVars, _context, session) => await config.mutationFn(vars, session),
    onError: (_err, _vars, context: ListMutationContext | undefined) => {
      context?.change?.rollback();
    },
    onMutate: async (vars: TVars) => {
      const session = captureMutationSession(qc);
      await qc.cancelQueries({ exact: true, queryKey: config.queryKey });
      session.throwIfAborted();
      const change = updateOptimisticList<TItem>(
        qc,
        config.queryKey,
        session,
        new Map([[config.variableId(vars), (existing) => config.buildItem(vars, existing)]]),
        config.itemId,
        true,
      );
      return { change };
    },
    onSettled: (_data, _error, _vars, context) => {
      if (!context?.change || context.change.settle()) {
        void qc.invalidateQueries({ exact: true, queryKey: config.queryKey });
      }
    },
  });
}
