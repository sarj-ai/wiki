"use client";

import { useQuery } from "@tanstack/react-query";

import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";
import type {
  DocIcon,
  DriveNode,
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
    queryFn: wikiApi.getDrives,
    queryKey: wikiKeys.drives,
    staleTime: TREE_STALE_MS,
  });
}

export function useDriveTree(driveId: null | string) {
  return useQuery<DriveTree>({
    enabled: !!driveId,
    queryFn: async () => wikiApi.getDriveFolders(driveId ?? ""),
    queryKey: wikiKeys.driveTree(driveId ?? "disabled"),
    staleTime: TREE_STALE_MS,
  });
}

export function useFile(fileId: null | string) {
  return useQuery<FileDetail>({
    enabled: !!fileId,
    queryFn: async () => wikiApi.getFile(fileId ?? ""),
    queryKey: wikiKeys.file(fileId ?? "disabled"),
  });
}

export function useFolderFiles(folderId: null | string, enabled: boolean) {
  return useQuery<DriveNode[]>({
    enabled: enabled && !!folderId,
    queryFn: async () => wikiApi.getFolderFiles(folderId ?? ""),
    queryKey: wikiKeys.folderFiles(folderId ?? "disabled"),
    staleTime: TREE_STALE_MS,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryFn: wikiApi.getPreferences,
    queryKey: wikiKeys.preferences,
    staleTime: TREE_STALE_MS,
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryFn: wikiApi.listFolderIcons,
    queryKey: wikiKeys.folderIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useDocIcons() {
  return useQuery<DocIcon[]>({
    queryFn: wikiApi.listDocIcons,
    queryKey: wikiKeys.docIcons,
    staleTime: TREE_STALE_MS,
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryFn: wikiApi.listSortOrders,
    queryKey: wikiKeys.sortOrders,
    staleTime: TREE_STALE_MS,
  });
}

export { wikiKeys } from "@/lib/wiki-keys";
