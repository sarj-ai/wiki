"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { SortOrder } from "@/types/navigator";

import { navigatorKeys } from "@/hooks/use-navigator-tree";
import { navigatorApi, type CreateFileInput, type PreferencesInput } from "@/lib/navigator-api";
import { injectNewFileIntoCaches, removeFileFromCaches } from "@/lib/navigator-cache";

// Drop the moved file's sort_order entry from the local cache. The
// backend does the same when ``move_file`` sees parent_folder_id
// change; doing it locally on onMutate avoids a transitional render
// where the moved row appears at its OLD sort position in the new
// destination before the server-side clear lands.
function dropSortOrderLocally(
  qc: ReturnType<typeof useQueryClient>,
  fileId: string,
): SortOrder[] | undefined {
  const previous = qc.getQueryData<SortOrder[]>(navigatorKeys.sortOrders);
  if (!previous) return undefined;
  qc.setQueryData<SortOrder[]>(
    navigatorKeys.sortOrders,
    previous.filter((s) => s.driveId !== fileId),
  );
  return previous;
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
      if (id) void qc.invalidateQueries({ queryKey: navigatorKeys.driveTree(id) });
    }
    // Folder-file listings inside moved folders may now be stale — drop
    // them all so the next expand refetches. Cheap vs scoped invalidation.
    void qc.invalidateQueries({ queryKey: ["navigator", "folder-files"] });
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
      void qc.invalidateQueries({ queryKey: ["navigator", "file", file.id] });
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
    }: {
      fileId: string;
      newParentFolderId: string;
      // Caller passes the source drive so onSuccess could invalidate
      // both source and destination tree caches if needed. Currently
      // unused — see the comment in onSuccess for why we skip the
      // tree invalidation entirely.
      sourceDriveId: string | null;
    }) => {
      const result = await navigatorApi.moveFile(fileId, newParentFolderId);
      return { result, sourceDriveId };
    },
    onMutate: ({ fileId }) => {
      // Optimistically drop the sort_order entry. Without this, the
      // moved row keeps its OLD-parent sort_order locally until the
      // post-success sortOrders invalidate refetches — which produces
      // a visible transition where the row first appears at its
      // stale sort position in the new destination, then snaps to
      // its real alpha position. Backend does the same clear on
      // commit; this just keeps the local cache in step.
      const previousSortOrders = dropSortOrderLocally(qc, fileId);
      return { previousSortOrders };
    },
    onError: (_err, _vars, context) => {
      // Restore on rollback so a failed move doesn't leave the user
      // missing a sort_order entry they had set before.
      if (context?.previousSortOrders) {
        qc.setQueryData(navigatorKeys.sortOrders, context.previousSortOrders);
      }
    },
    onSuccess: ({ result }) => {
      // DO NOT invalidate drive-tree or folder-files here. Drive's
      // listing index lags ``files.update`` (move) by a few seconds,
      // so an immediate refetch returns listings that still show the
      // file in its old parent — overwriting the optimistic insert
      // and making the just-moved row disappear from the UI for ~5s
      // until the user refreshes. Same rationale as ``useCreateFile``
      // and ``useCopyFile``. The optimistic update via
      // ``optimisticMoveFile`` is the source of truth until React
      // Query's stale-while-revalidate cycle quietly resyncs in the
      // background once the index has caught up.
      //
      // File metadata (the breadcrumb endpoint) and sortOrders
      // aren't subject to the same lag — they go through our own
      // services, not Drive's listing — so invalidating those is
      // safe and keeps the moved file's detail page in sync.
      void qc.invalidateQueries({ queryKey: ["navigator", "file", result.id] });
      void qc.invalidateQueries({ queryKey: navigatorKeys.sortOrders });
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
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      fileId,
      driveId: _driveId,
      parentFolderId: _parentFolderId,
    }: {
      fileId: string;
      driveId: string | null;
      parentFolderId: string | null;
    }) => navigatorApi.deleteFile(fileId),
    onSuccess: (_void, { fileId, driveId, parentFolderId }) => {
      removeFileFromCaches(qc, fileId, parentFolderId, driveId);
    },
  });
}

export function useBulkSetSortOrders() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: SortOrder[]) => navigatorApi.bulkSetSortOrders(items),
    // Optimistic update: write the new orders into the cache before
    // the server round-trip so the sidebar re-renders in the final
    // order on the same tick dnd-kit clears its drag transform.
    // IMPORTANT: this callback must stay synchronous. An ``await``
    // inside ``onMutate`` — even ``qc.cancelQueries`` — defers the
    // ``setQueryData`` call by a microtask, so React commits one
    // render with the OLD orders first. Visually that reads as the
    // dropped row flashing back to its original slot before snapping
    // to the new one, i.e., "movement down then back up". Skipping
    // ``cancelQueries`` is safe here because we also invalidate in
    // ``onSettled``; the refetch there races with nothing.
    onMutate: (items) => {
      const previous = qc.getQueryData<SortOrder[]>(navigatorKeys.sortOrders) ?? [];
      const byId = new Map(previous.map((s) => [s.driveId, s] as const));
      for (const item of items) byId.set(item.driveId, item);
      qc.setQueryData<SortOrder[]>(navigatorKeys.sortOrders, Array.from(byId.values()));
      return { previous };
    },
    onError: (_err, _items, context) => {
      if (context) {
        qc.setQueryData(navigatorKeys.sortOrders, context.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: navigatorKeys.sortOrders });
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
      void qc.invalidateQueries({ queryKey: navigatorKeys.docIcons });
    },
  });
}

export function useSetFolderIcon() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ folderDriveId, icon }: { folderDriveId: string; icon: string | null }) =>
      navigatorApi.setFolderIcon(folderDriveId, icon),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: navigatorKeys.folderIcons });
    },
  });
}
