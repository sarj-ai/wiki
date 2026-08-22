"use client";

import { type InfiniteData, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { wikiKeys } from "@/lib/wiki-keys";
import { wikiReadApi } from "@/lib/wiki-read-client";
import type {
  DocIcon,
  DriveNode,
  DrivePermissionsPage,
  DriveSummary,
  DriveTree,
  FileDetail,
  FolderIcon,
  Preferences,
  SortOrder,
} from "@/types/wiki";

const TREE_STALE_MS = 5 * 60 * 1000;

export function useDrives() {
  return useQuery<DriveSummary[]>({
    queryFn: wikiReadApi.getDrives,
    queryKey: wikiKeys.drives,
    staleTime: TREE_STALE_MS,
  });
}

export function useDriveTree(driveId: null | string, enabled = true) {
  return useQuery(driveTreeQueryOptions(driveId, enabled));
}

export function driveTreeQueryOptions(driveId: null | string, enabled: boolean) {
  return queryOptions<DriveTree>({
    enabled: enabled && !!driveId,
    queryFn: async () => await wikiReadApi.getDriveFolders(driveId ?? ""),
    queryKey: wikiKeys.driveTree(driveId ?? "disabled"),
    staleTime: TREE_STALE_MS,
  });
}

export function useDrivePermissions(driveId: null | string, enabled: boolean) {
  return useInfiniteQuery<
    DrivePermissionsPage,
    Error,
    InfiniteData<DrivePermissionsPage>,
    readonly ["drive-permissions", string],
    string | undefined
  >({
    enabled: enabled && !!driveId,
    getNextPageParam: (lastPage) => lastPage.nextPageToken,
    initialPageParam: undefined,
    queryFn: async ({ pageParam }) =>
      await wikiReadApi.getDrivePermissions(driveId ?? "", pageParam),
    queryKey: ["drive-permissions", driveId ?? "disabled"] as const,
    staleTime: 0,
  });
}

export function useFile(fileId: null | string) {
  return useQuery<FileDetail>({
    enabled: !!fileId,
    meta: { foregroundPrimaryLoad: true },
    queryFn: async () => await wikiReadApi.getFile(fileId ?? ""),
    queryKey: wikiKeys.file(fileId ?? "disabled"),
  });
}

export function useFolderFiles(folderId: null | string, enabled: boolean) {
  return useQuery<DriveNode[]>({
    enabled: enabled && !!folderId,
    queryFn: async () => await wikiReadApi.getFolderFiles(folderId ?? ""),
    queryKey: wikiKeys.folderFiles(folderId ?? "disabled"),
    staleTime: TREE_STALE_MS,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryFn: wikiReadApi.getPreferences,
    queryKey: wikiKeys.preferences,
    staleTime: TREE_STALE_MS,
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryFn: wikiReadApi.listFolderIcons,
    queryKey: wikiKeys.folderIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useDocIcons() {
  return useQuery<DocIcon[]>({
    queryFn: wikiReadApi.listDocIcons,
    queryKey: wikiKeys.docIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryFn: wikiReadApi.listSortOrders,
    queryKey: wikiKeys.sortOrders,
    staleTime: TREE_STALE_MS,
  });
}

export { wikiKeys } from "@/lib/wiki-keys";
