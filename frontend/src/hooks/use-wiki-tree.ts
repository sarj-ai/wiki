"use client";

import { useQuery } from "@tanstack/react-query";

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

import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";

export { wikiKeys };

const TREE_STALE_MS = 5 * 60 * 1000;

export function useDrives() {
  return useQuery<DriveSummary[]>({
    queryKey: wikiKeys.drives,
    queryFn: wikiApi.getDrives,
    staleTime: TREE_STALE_MS,
  });
}

export function useDriveTree(driveId: string | null) {
  return useQuery<DriveTree>({
    queryKey: wikiKeys.driveTree(driveId ?? "disabled"),
    queryFn: () => wikiApi.getDriveFolders(driveId ?? ""),
    enabled: !!driveId,
    staleTime: TREE_STALE_MS,
  });
}

export function useFile(fileId: string | null) {
  return useQuery<FileDetail>({
    queryKey: wikiKeys.file(fileId ?? "disabled"),
    queryFn: () => wikiApi.getFile(fileId ?? ""),
    enabled: !!fileId,
  });
}

export function useFolderFiles(folderId: string | null, enabled: boolean) {
  return useQuery<DriveNode[]>({
    queryKey: wikiKeys.folderFiles(folderId ?? "disabled"),
    queryFn: () => wikiApi.getFolderFiles(folderId ?? ""),
    enabled: enabled && !!folderId,
    staleTime: TREE_STALE_MS,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryKey: wikiKeys.preferences,
    queryFn: wikiApi.getPreferences,
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryKey: wikiKeys.folderIcons,
    queryFn: wikiApi.listFolderIcons,
  });
}

export function useDocIcons() {
  return useQuery<DocIcon[]>({
    queryKey: wikiKeys.docIcons,
    queryFn: wikiApi.listDocIcons,
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryKey: wikiKeys.sortOrders,
    queryFn: wikiApi.listSortOrders,
  });
}
