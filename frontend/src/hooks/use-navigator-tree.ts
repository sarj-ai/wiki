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
} from "@/types/navigator";

import { navigatorApi } from "@/lib/navigator-api";

// Stable query keys so mutations elsewhere (D4, D5) can invalidate by prefix.
export const navigatorKeys = {
  drives: ["navigator", "drives"] as const,
  driveTree: (driveId: string) => ["navigator", "drive-tree", driveId] as const,
  folderFiles: (folderId: string) => ["navigator", "folder-files", folderId] as const,
  file: (fileId: string) => ["navigator", "file", fileId] as const,
  preferences: ["navigator", "preferences"] as const,
  folderIcons: ["navigator", "folder-icons"] as const,
  docIcons: ["navigator", "doc-icons"] as const,
  sortOrders: ["navigator", "sort-orders"] as const,
};

export function useDrives() {
  return useQuery<DriveSummary[]>({
    queryKey: navigatorKeys.drives,
    queryFn: () => navigatorApi.getDrives(),
  });
}

export function useDriveTree(driveId: string | null) {
  return useQuery<DriveTree>({
    queryKey: driveId ? navigatorKeys.driveTree(driveId) : ["navigator", "drive-tree", "disabled"],
    queryFn: () => navigatorApi.getDriveFolders(driveId ?? ""),
    enabled: !!driveId,
  });
}

export function useFile(fileId: string | null) {
  return useQuery<FileDetail>({
    queryKey: fileId ? navigatorKeys.file(fileId) : ["navigator", "file", "disabled"],
    queryFn: () => navigatorApi.getFile(fileId ?? ""),
    enabled: !!fileId,
  });
}

export function useFolderFiles(folderId: string | null, enabled: boolean) {
  return useQuery<DriveNode[]>({
    queryKey: folderId
      ? navigatorKeys.folderFiles(folderId)
      : ["navigator", "folder-files", "disabled"],
    queryFn: () => navigatorApi.getFolderFiles(folderId ?? ""),
    enabled: enabled && !!folderId,
  });
}

export function usePreferences() {
  return useQuery<Preferences>({
    queryKey: navigatorKeys.preferences,
    queryFn: () => navigatorApi.getPreferences(),
  });
}

export function useFolderIcons() {
  return useQuery<FolderIcon[]>({
    queryKey: navigatorKeys.folderIcons,
    queryFn: () => navigatorApi.listFolderIcons(),
  });
}

export function useDocIcons() {
  return useQuery<DocIcon[]>({
    queryKey: navigatorKeys.docIcons,
    queryFn: () => navigatorApi.listDocIcons(),
  });
}

export function useSortOrders() {
  return useQuery<SortOrder[]>({
    queryKey: navigatorKeys.sortOrders,
    queryFn: () => navigatorApi.listSortOrders(),
  });
}
