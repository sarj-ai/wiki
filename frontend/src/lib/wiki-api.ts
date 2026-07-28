import * as z from "zod";

import { ApiError, makeCall } from "@/lib/api-client";
import type { Client } from "@/lib/generated/client";
import {
  bootstrapBootstrapGet,
  bulkSetSortOrdersExtrasSortOrderPut,
  copyFileFilesFileIdCopyPost,
  createFileFilesPost,
  deleteFileFilesFileIdDelete,
  deleteSortOrderExtrasSortOrderDriveIdDelete,
  getDriveFoldersSidebarDrivesDriveIdFoldersGet,
  getDrivesSidebarDrivesGet,
  getFolderFilesSidebarFoldersFolderIdFilesGet,
  listDocIconsExtrasDocIconsGet,
  listMyFolderIconsExtrasFolderIconsGet,
  listMySortOrdersExtrasSortOrderGet,
  meAuthMeGet,
  moveFileFilesFileIdMovePatch,
  readDocIconExtrasDocIconsDriveIdGet,
  readFileFilesFileIdGet,
  readPreferencesExtrasPreferencesGet,
  refreshDriveFoldersSidebarDrivesDriveIdRefreshPost,
  renameFileFilesFileIdRenamePatch,
  searchDriveSearchGet,
  setDocIconExtrasDocIconsDriveIdPut,
  setFolderIconExtrasFolderIconsFolderDriveIdPut,
  snippetDriveSearchSnippetGet,
  writePreferencesExtrasPreferencesPut,
} from "@/lib/generated/sdk.gen";
import {
  bootstrapRead,
  docIcon,
  driveNode,
  driveSummary,
  driveTree,
  fileDetail,
  fileResponse,
  folderIcon,
  preferences,
  searchResult,
  snippetResult,
  sortOrder,
  sortOrderBulkResult,
  userProfile,
} from "@/lib/wiki-schemas";
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

export { ApiError };

export interface CreateFileInput {
  mimeType: string;
  name: string;
  parentFolderId: string;
}

export interface DocIconInput {
  coverUrl?: null | string;
  icon?: null | string;
}

export interface PreferencesInput {
  hiddenWorkspaceIds?: string[];
  showNonDocFiles?: boolean;
}

export type UserProfile = z.output<typeof userProfile>;
export type Bootstrap = z.output<typeof bootstrapRead>;

export function makeWikiApi(client?: Client) {
  const call = makeCall(client);
  return {
    bulkSetSortOrders: async (items: SortOrder[]): Promise<{ updated: number }> =>
      call(
        bulkSetSortOrdersExtrasSortOrderPut,
        {
          body: {
            items: items.map((item) => ({ drive_id: item.driveId, sort_order: item.sortOrder })),
          },
        },
        sortOrderBulkResult,
      ),

    clearSortOrder: async (driveId: string): Promise<void> =>
      call(deleteSortOrderExtrasSortOrderDriveIdDelete, { path: { drive_id: driveId } }),

    copyFile: async (fileId: string, name: string): Promise<FileResponse> =>
      call(
        copyFileFilesFileIdCopyPost,
        { body: { name }, path: { file_id: fileId } },
        fileResponse,
      ),

    createFile: async (input: CreateFileInput): Promise<FileResponse> =>
      call(
        createFileFilesPost,
        {
          body: {
            mime_type: input.mimeType,
            name: input.name,
            parent_folder_id: input.parentFolderId,
          },
        },
        fileResponse,
      ),

    deleteFile: async (fileId: string): Promise<void> =>
      call(deleteFileFilesFileIdDelete, { path: { file_id: fileId } }),

    driveSearch: async (query: string, limitPerDrive?: number): Promise<DriveSearchResult[]> =>
      call(
        searchDriveSearchGet,
        {
          query: {
            q: query,
            ...(limitPerDrive === undefined ? {} : { limit_per_drive: limitPerDrive }),
          },
        },
        z.array(searchResult),
      ),

    driveSearchSnippet: async (
      fileId: string,
      query: string,
      mimeType?: string,
    ): Promise<SnippetResult> =>
      call(
        snippetDriveSearchSnippetGet,
        {
          query: {
            file_id: fileId,
            q: query,
            ...(mimeType === undefined ? {} : { mime_type: mimeType }),
          },
        },
        snippetResult,
      ),

    getBootstrap: async (): Promise<Bootstrap> => call(bootstrapBootstrapGet, {}, bootstrapRead),

    getDocIcon: async (driveId: string): Promise<DocIcon> =>
      call(readDocIconExtrasDocIconsDriveIdGet, { path: { drive_id: driveId } }, docIcon),

    getDriveFolders: async (driveId: string): Promise<DriveTree> =>
      call(
        getDriveFoldersSidebarDrivesDriveIdFoldersGet,
        { path: { drive_id: driveId } },
        driveTree,
      ),

    getDrives: async (): Promise<DriveSummary[]> =>
      call(getDrivesSidebarDrivesGet, {}, z.array(driveSummary)),

    getFile: async (fileId: string): Promise<FileDetail> =>
      call(readFileFilesFileIdGet, { path: { file_id: fileId } }, fileDetail),

    getFolderFiles: async (folderId: string): Promise<DriveNode[]> =>
      call(
        getFolderFilesSidebarFoldersFolderIdFilesGet,
        { path: { folder_id: folderId } },
        z.array(driveNode),
      ),

    getMe: async (): Promise<UserProfile> => call(meAuthMeGet, {}, userProfile),

    getPreferences: async (): Promise<Preferences> =>
      call(readPreferencesExtrasPreferencesGet, {}, preferences),

    listDocIcons: async (): Promise<DocIcon[]> =>
      call(listDocIconsExtrasDocIconsGet, {}, z.array(docIcon)),

    listFolderIcons: async (): Promise<FolderIcon[]> =>
      call(listMyFolderIconsExtrasFolderIconsGet, {}, z.array(folderIcon)),

    listSortOrders: async (): Promise<SortOrder[]> =>
      call(listMySortOrdersExtrasSortOrderGet, {}, z.array(sortOrder)),

    moveFile: async (fileId: string, newParentFolderId: string): Promise<FileResponse> =>
      call(
        moveFileFilesFileIdMovePatch,
        { body: { new_parent_folder_id: newParentFolderId }, path: { file_id: fileId } },
        fileResponse,
      ),

    refreshDriveFolders: async (driveId: string): Promise<DriveTree> =>
      call(
        refreshDriveFoldersSidebarDrivesDriveIdRefreshPost,
        { path: { drive_id: driveId } },
        driveTree,
      ),

    renameFile: async (fileId: string, name: string): Promise<FileResponse> =>
      call(
        renameFileFilesFileIdRenamePatch,
        { body: { name }, path: { file_id: fileId } },
        fileResponse,
      ),

    setDocIcon: async (driveId: string, payload: DocIconInput): Promise<DocIcon> =>
      call(
        setDocIconExtrasDocIconsDriveIdPut,
        {
          body: { cover_url: payload.coverUrl ?? null, icon: payload.icon ?? null },
          path: { drive_id: driveId },
        },
        docIcon,
      ),

    setFolderIcon: async (folderDriveId: string, icon: null | string): Promise<FolderIcon> =>
      call(
        setFolderIconExtrasFolderIconsFolderDriveIdPut,
        { body: { icon }, path: { folder_drive_id: folderDriveId } },
        folderIcon,
      ),

    setPreferences: async (payload: PreferencesInput): Promise<Preferences> =>
      call(
        writePreferencesExtrasPreferencesPut,
        {
          body: {
            ...(payload.hiddenWorkspaceIds === undefined
              ? {}
              : { hidden_workspace_ids: payload.hiddenWorkspaceIds }),
            show_non_doc_files: payload.showNonDocFiles ?? null,
          },
        },
        preferences,
      ),
  };
}

export const wikiApi = makeWikiApi();
