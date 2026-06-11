import * as z from "zod";

import type {
  DocIcon,
  DriveNode,
  DriveSearchResult,
  DriveSummary,
  DriveTree,
  FileDetail,
  FileResponse,
  FolderIcon,
  Preferences,
  SnippetResult,
  SortOrder,
} from "@/types/wiki";

import { apiClient, validatedJson } from "@/lib/api-client";
import {
  zDocIconRead,
  zDriveNode,
  zDriveSummary,
  zDriveTree,
  zFileDetail,
  zFileResponse,
  zFolderIconRead,
  zPreferencesRead,
  zSearchResult,
  zSnippetResult,
  zSortOrderBulkResult,
  zSortOrderRead,
} from "@/lib/generated/zod.gen";

// Re-export so existing call sites that catch ApiError keep working.
export { ApiError } from "@/lib/api-client";

export interface CreateFileInput {
  parentFolderId: string;
  name: string;
  mimeType: string;
}

interface DocIconInput {
  icon?: string | null;
  coverUrl?: string | null;
}

export interface PreferencesInput {
  showNonDocFiles?: boolean;
  hiddenWorkspaceIds?: string[];
}

const enc = encodeURIComponent;

// Route table — each entry is a thin wrapper that hands off to
// ``apiClient``. Bodies stay in camelCase here; the client snakecases
// them before they hit FastAPI. The explicit ``Promise<Interface>``
// annotations make schema↔interface drift a compile error.
export const wikiApi = {
  // ---- Sidebar ---------------------------------------------------------
  getDrives: (): Promise<DriveSummary[]> =>
    validatedJson(apiClient.get("sidebar/drives"), z.array(zDriveSummary)),

  getDriveFolders: (driveId: string): Promise<DriveTree> =>
    validatedJson(apiClient.get(`sidebar/drives/${enc(driveId)}/folders`), zDriveTree),

  refreshDriveFolders: (driveId: string): Promise<DriveTree> =>
    validatedJson(apiClient.post(`sidebar/drives/${enc(driveId)}/refresh`), zDriveTree),

  getFolderFiles: (folderId: string): Promise<DriveNode[]> =>
    validatedJson(apiClient.get(`sidebar/folders/${enc(folderId)}/files`), z.array(zDriveNode)),

  // ---- Files -----------------------------------------------------------
  getFile: (fileId: string): Promise<FileDetail> =>
    validatedJson(apiClient.get(`files/${enc(fileId)}`), zFileDetail),

  createFile: (data: CreateFileInput): Promise<FileResponse> =>
    validatedJson(apiClient.post("files", { json: data }), zFileResponse),

  renameFile: (fileId: string, name: string): Promise<FileResponse> =>
    validatedJson(
      apiClient.patch(`files/${enc(fileId)}/rename`, { json: { name } }),
      zFileResponse,
    ),

  moveFile: (fileId: string, newParentFolderId: string): Promise<FileResponse> =>
    validatedJson(
      apiClient.patch(`files/${enc(fileId)}/move`, { json: { newParentFolderId } }),
      zFileResponse,
    ),

  copyFile: (fileId: string, name: string): Promise<FileResponse> =>
    validatedJson(apiClient.post(`files/${enc(fileId)}/copy`, { json: { name } }), zFileResponse),

  deleteFile: async (fileId: string): Promise<void> => {
    await apiClient.delete(`files/${enc(fileId)}`);
  },

  // ---- Extras: doc icons (global) --------------------------------------
  listDocIcons: (): Promise<DocIcon[]> =>
    validatedJson(apiClient.get("extras/doc-icons"), z.array(zDocIconRead)),

  getDocIcon: (driveId: string): Promise<DocIcon> =>
    validatedJson(apiClient.get(`extras/doc-icons/${enc(driveId)}`), zDocIconRead),

  setDocIcon: (driveId: string, payload: DocIconInput): Promise<DocIcon> =>
    validatedJson(
      apiClient.put(`extras/doc-icons/${enc(driveId)}`, {
        json: { icon: payload.icon ?? null, coverUrl: payload.coverUrl ?? null },
      }),
      zDocIconRead,
    ),

  // ---- Extras: folder icons (per-user) ---------------------------------
  listFolderIcons: (): Promise<FolderIcon[]> =>
    validatedJson(apiClient.get("extras/folder-icons"), z.array(zFolderIconRead)),

  setFolderIcon: (folderDriveId: string, icon: string | null): Promise<FolderIcon> =>
    validatedJson(
      apiClient.put(`extras/folder-icons/${enc(folderDriveId)}`, { json: { icon } }),
      zFolderIconRead,
    ),

  // ---- Extras: sort order (per-user) -----------------------------------
  listSortOrders: (): Promise<SortOrder[]> =>
    validatedJson(apiClient.get("extras/sort-order"), z.array(zSortOrderRead)),

  bulkSetSortOrders: (items: SortOrder[]): Promise<{ updated: number }> =>
    validatedJson(apiClient.put("extras/sort-order", { json: { items } }), zSortOrderBulkResult),

  clearSortOrder: async (driveId: string): Promise<void> => {
    await apiClient.delete(`extras/sort-order/${enc(driveId)}`);
  },

  // ---- Extras: preferences (per-user) ----------------------------------
  getPreferences: (): Promise<Preferences> =>
    validatedJson(apiClient.get("extras/preferences"), zPreferencesRead),

  setPreferences: (payload: PreferencesInput): Promise<Preferences> =>
    validatedJson(apiClient.put("extras/preferences", { json: payload }), zPreferencesRead),

  // ---- Drive Search ----------------------------------------------------
  driveSearch: (query: string, limitPerDrive?: number): Promise<DriveSearchResult[]> =>
    validatedJson(
      apiClient.get("drive-search", {
        searchParams: {
          q: query,
          ...(limitPerDrive === undefined ? {} : { limit_per_drive: limitPerDrive }),
        },
      }),
      z.array(zSearchResult),
    ),

  driveSearchSnippet: (fileId: string, query: string, mimeType?: string): Promise<SnippetResult> =>
    validatedJson(
      apiClient.get("drive-search/snippet", {
        searchParams: {
          file_id: fileId,
          q: query,
          ...(mimeType ? { mime_type: mimeType } : {}),
        },
      }),
      zSnippetResult,
    ),
};
