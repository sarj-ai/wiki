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

import { apiClient } from "@/lib/api-client";

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
// them before they hit FastAPI, and camelcases the response back.
export const wikiApi = {
  // ---- Sidebar ---------------------------------------------------------
  getDrives: () => apiClient.get("sidebar/drives").json<DriveSummary[]>(),

  getDriveFolders: (driveId: string) =>
    apiClient.get(`sidebar/drives/${enc(driveId)}/folders`).json<DriveTree>(),

  refreshDriveFolders: (driveId: string) =>
    apiClient.post(`sidebar/drives/${enc(driveId)}/refresh`).json<DriveTree>(),

  getFolderFiles: (folderId: string) =>
    apiClient.get(`sidebar/folders/${enc(folderId)}/files`).json<DriveNode[]>(),

  // ---- Files -----------------------------------------------------------
  getFile: (fileId: string) => apiClient.get(`files/${enc(fileId)}`).json<FileDetail>(),

  createFile: (data: CreateFileInput) =>
    apiClient.post("files", { json: data }).json<FileResponse>(),

  renameFile: (fileId: string, name: string) =>
    apiClient.patch(`files/${enc(fileId)}/rename`, { json: { name } }).json<FileResponse>(),

  moveFile: (fileId: string, newParentFolderId: string) =>
    apiClient
      .patch(`files/${enc(fileId)}/move`, { json: { newParentFolderId } })
      .json<FileResponse>(),

  copyFile: (fileId: string, name: string) =>
    apiClient.post(`files/${enc(fileId)}/copy`, { json: { name } }).json<FileResponse>(),

  deleteFile: async (fileId: string): Promise<void> => {
    await apiClient.delete(`files/${enc(fileId)}`);
  },

  // ---- Extras: doc icons (global) --------------------------------------
  listDocIcons: () => apiClient.get("extras/doc-icons").json<DocIcon[]>(),

  getDocIcon: (driveId: string) =>
    apiClient.get(`extras/doc-icons/${enc(driveId)}`).json<DocIcon>(),

  setDocIcon: (driveId: string, payload: DocIconInput) =>
    apiClient
      .put(`extras/doc-icons/${enc(driveId)}`, {
        json: { icon: payload.icon ?? null, coverUrl: payload.coverUrl ?? null },
      })
      .json<DocIcon>(),

  // ---- Extras: folder icons (per-user) ---------------------------------
  listFolderIcons: () => apiClient.get("extras/folder-icons").json<FolderIcon[]>(),

  setFolderIcon: (folderDriveId: string, icon: string | null) =>
    apiClient
      .put(`extras/folder-icons/${enc(folderDriveId)}`, { json: { icon } })
      .json<FolderIcon>(),

  // ---- Extras: sort order (per-user) -----------------------------------
  listSortOrders: () => apiClient.get("extras/sort-order").json<SortOrder[]>(),

  bulkSetSortOrders: (items: SortOrder[]) =>
    apiClient.put("extras/sort-order", { json: { items } }).json<{ updated: number }>(),

  clearSortOrder: async (driveId: string): Promise<void> => {
    await apiClient.delete(`extras/sort-order/${enc(driveId)}`);
  },

  // ---- Extras: preferences (per-user) ----------------------------------
  getPreferences: () => apiClient.get("extras/preferences").json<Preferences>(),

  setPreferences: (payload: PreferencesInput) =>
    apiClient.put("extras/preferences", { json: payload }).json<Preferences>(),

  // ---- Drive Search ----------------------------------------------------
  driveSearch: (query: string, limitPerDrive?: number) =>
    apiClient
      .get("drive-search", {
        searchParams: {
          q: query,
          ...(limitPerDrive === undefined ? {} : { limit_per_drive: limitPerDrive }),
        },
      })
      .json<DriveSearchResult[]>(),

  driveSearchSnippet: (fileId: string, query: string) =>
    apiClient
      .get("drive-search/snippet", { searchParams: { file_id: fileId, q: query } })
      .json<SnippetResult>(),
};
