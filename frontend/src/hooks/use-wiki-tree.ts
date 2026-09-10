"use client";

import { type InfiniteData, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { wikiKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
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
    queryFn: WIKI_READ_API.getDrives,
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
    queryFn: async () => await WIKI_READ_API.getDriveFolders(driveId ?? ""),
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
      await WIKI_READ_API.getDrivePermissions(driveId ?? "", pageParam),
    queryKey: ["drive-permissions", driveId ?? "disabled"] as const,
    staleTime: 0,
  });
}

export function useFile(fileId: null | string) {
  return useQuery({
    ...fileQueryOptions(fileId ?? "disabled"),
    enabled: !!fileId,
  });
}

export function fileQueryOptions(fileId: string) {
  return queryOptions<FileDetail>({
    queryFn: async () => await WIKI_READ_API.getFile(fileId),
    queryKey: wikiKeys.file(fileId),
  });
}

export function useFolderFiles(folderId: null | string, enabled: boolean) {
  return useQuery({
    ...folderFilesQueryOptions(folderId ?? "disabled"),
    enabled: enabled && !!folderId,
  });
}

export function folderFilesQueryOptions(folderId: string) {
  return queryOptions<DriveNode[]>({
    queryFn: async () => await WIKI_READ_API.getFolderFiles(folderId),
    queryKey: wikiKeys.folderFiles(folderId),
    staleTime: TREE_STALE_MS,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryFn: WIKI_READ_API.getPreferences,
    queryKey: wikiKeys.preferences,
    staleTime: TREE_STALE_MS,
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryFn: WIKI_READ_API.listFolderIcons,
    queryKey: wikiKeys.folderIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useDocIcons() {
  return useQuery<DocIcon[]>({
    queryFn: WIKI_READ_API.listDocIcons,
    queryKey: wikiKeys.docIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryFn: WIKI_READ_API.listSortOrders,
    queryKey: wikiKeys.sortOrders,
    staleTime: TREE_STALE_MS,
  });
}

export { wikiKeys } from "@/lib/wiki-keys";
