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

// Stable query keys so mutations elsewhere (D4, D5) can invalidate by prefix.
export const wikiKeys = {
  drives: ["wiki", "drives"] as const,
  driveTree: (driveId: string) => ["wiki", "drive-tree", driveId] as const,
  folderFiles: (folderId: string) => ["wiki", "folder-files", folderId] as const,
  file: (fileId: string) => ["wiki", "file", fileId] as const,
  preferences: ["wiki", "preferences"] as const,
  folderIcons: ["wiki", "folder-icons"] as const,
  docIcons: ["wiki", "doc-icons"] as const,
  sortOrders: ["wiki", "sort-orders"] as const,
};

export function useDrives() {
  return useQuery<DriveSummary[]>({
    queryKey: wikiKeys.drives,
    queryFn: () => wikiApi.getDrives(),
  });
}

export function useDriveTree(driveId: string | null) {
  return useQuery<DriveTree>({
    queryKey: driveId ? wikiKeys.driveTree(driveId) : ["wiki", "drive-tree", "disabled"],
    queryFn: () => wikiApi.getDriveFolders(driveId ?? ""),
    enabled: !!driveId,
  });
}

export function useFile(fileId: string | null) {
  return useQuery<FileDetail>({
    queryKey: fileId ? wikiKeys.file(fileId) : ["wiki", "file", "disabled"],
    queryFn: () => wikiApi.getFile(fileId ?? ""),
    enabled: !!fileId,
  });
}

export function useFolderFiles(folderId: string | null, enabled: boolean) {
  return useQuery<DriveNode[]>({
    queryKey: folderId ? wikiKeys.folderFiles(folderId) : ["wiki", "folder-files", "disabled"],
    queryFn: () => wikiApi.getFolderFiles(folderId ?? ""),
    enabled: enabled && !!folderId,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryKey: wikiKeys.preferences,
    queryFn: () => wikiApi.getPreferences(),
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryKey: wikiKeys.folderIcons,
    queryFn: () => wikiApi.listFolderIcons(),
  });
}

export function useDocIcons() {
  return useQuery<DocIcon[]>({
    queryKey: wikiKeys.docIcons,
    queryFn: () => wikiApi.listDocIcons(),
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryKey: wikiKeys.sortOrders,
    queryFn: () => wikiApi.listSortOrders(),
  });
}
