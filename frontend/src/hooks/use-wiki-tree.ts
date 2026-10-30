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
    queryFn: async ({ signal }) => await WIKI_READ_API.getDrives(signal),
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
    queryFn: async ({ signal }) => await WIKI_READ_API.getDriveFolders(driveId ?? "", signal),
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
    queryFn: async ({ pageParam, signal }) =>
      await WIKI_READ_API.getDrivePermissions(driveId ?? "", pageParam, signal),
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
    queryFn: async ({ signal }) => await WIKI_READ_API.getFile(fileId, signal),
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
    queryFn: async ({ signal }) => await WIKI_READ_API.getFolderFiles(folderId, signal),
    queryKey: wikiKeys.folderFiles(folderId),
    staleTime: TREE_STALE_MS,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryFn: async ({ signal }) => await WIKI_READ_API.getPreferences(signal),
    queryKey: wikiKeys.preferences,
    staleTime: TREE_STALE_MS,
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryFn: async ({ signal }) => await WIKI_READ_API.listFolderIcons(signal),
    queryKey: wikiKeys.folderIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useDocIcons(enabled = true) {
  return useQuery<DocIcon[]>({
    enabled,
    queryFn: async ({ signal }) => await WIKI_READ_API.listDocIcons(signal),
    queryKey: wikiKeys.docIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryFn: async ({ signal }) => await WIKI_READ_API.listSortOrders(signal),
    queryKey: wikiKeys.sortOrders,
    staleTime: TREE_STALE_MS,
  });
}

export { wikiKeys } from "@/lib/wiki-keys";
