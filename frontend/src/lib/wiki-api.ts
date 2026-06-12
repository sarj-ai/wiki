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

import { ApiError, makeCall } from "@/lib/api-client";
import { type Client } from "@/lib/generated/client";
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

export { ApiError };

export interface CreateFileInput {
  parentFolderId: string;
  name: string;
  mimeType: string;
}

export interface DocIconInput {
  icon?: string | null;
  coverUrl?: string | null;
}

export interface PreferencesInput {
  showNonDocFiles?: boolean;
  hiddenWorkspaceIds?: string[];
}

export type UserProfile = z.output<typeof userProfile>;
export type Bootstrap = z.output<typeof bootstrapRead>;

export function makeWikiApi(client?: Client) {
  const call = makeCall(client);
  return {
    getDrives: (): Promise<DriveSummary[]> =>
      call(getDrivesSidebarDrivesGet, {}, z.array(driveSummary)),

    getDriveFolders: (driveId: string): Promise<DriveTree> =>
      call(
        getDriveFoldersSidebarDrivesDriveIdFoldersGet,
        { path: { drive_id: driveId } },
        driveTree,
      ),

    refreshDriveFolders: (driveId: string): Promise<DriveTree> =>
      call(
        refreshDriveFoldersSidebarDrivesDriveIdRefreshPost,
        { path: { drive_id: driveId } },
        driveTree,
      ),

    getFolderFiles: (folderId: string): Promise<DriveNode[]> =>
      call(
        getFolderFilesSidebarFoldersFolderIdFilesGet,
        { path: { folder_id: folderId } },
        z.array(driveNode),
      ),

    getFile: (fileId: string): Promise<FileDetail> =>
      call(readFileFilesFileIdGet, { path: { file_id: fileId } }, fileDetail),

    createFile: (input: CreateFileInput): Promise<FileResponse> =>
      call(
        createFileFilesPost,
        {
          body: {
            parent_folder_id: input.parentFolderId,
            name: input.name,
            mime_type: input.mimeType,
          },
        },
        fileResponse,
      ),

    renameFile: (fileId: string, name: string): Promise<FileResponse> =>
      call(
        renameFileFilesFileIdRenamePatch,
        { path: { file_id: fileId }, body: { name } },
        fileResponse,
      ),

    moveFile: (fileId: string, newParentFolderId: string): Promise<FileResponse> =>
      call(
        moveFileFilesFileIdMovePatch,
        { path: { file_id: fileId }, body: { new_parent_folder_id: newParentFolderId } },
        fileResponse,
      ),

    copyFile: (fileId: string, name: string): Promise<FileResponse> =>
      call(
        copyFileFilesFileIdCopyPost,
        { path: { file_id: fileId }, body: { name } },
        fileResponse,
      ),

    deleteFile: (fileId: string): Promise<void> =>
      call(deleteFileFilesFileIdDelete, { path: { file_id: fileId } }),

    listDocIcons: (): Promise<DocIcon[]> =>
      call(listDocIconsExtrasDocIconsGet, {}, z.array(docIcon)),

    getDocIcon: (driveId: string): Promise<DocIcon> =>
      call(readDocIconExtrasDocIconsDriveIdGet, { path: { drive_id: driveId } }, docIcon),

    setDocIcon: (driveId: string, payload: DocIconInput): Promise<DocIcon> =>
      call(
        setDocIconExtrasDocIconsDriveIdPut,
        {
          path: { drive_id: driveId },
          body: { icon: payload.icon ?? null, cover_url: payload.coverUrl ?? null },
        },
        docIcon,
      ),

    listFolderIcons: (): Promise<FolderIcon[]> =>
      call(listMyFolderIconsExtrasFolderIconsGet, {}, z.array(folderIcon)),

    setFolderIcon: (folderDriveId: string, icon: string | null): Promise<FolderIcon> =>
      call(
        setFolderIconExtrasFolderIconsFolderDriveIdPut,
        { path: { folder_drive_id: folderDriveId }, body: { icon } },
        folderIcon,
      ),

    listSortOrders: (): Promise<SortOrder[]> =>
      call(listMySortOrdersExtrasSortOrderGet, {}, z.array(sortOrder)),

    bulkSetSortOrders: (items: SortOrder[]): Promise<{ updated: number }> =>
      call(
        bulkSetSortOrdersExtrasSortOrderPut,
        {
          body: {
            items: items.map((item) => ({ drive_id: item.driveId, sort_order: item.sortOrder })),
          },
        },
        sortOrderBulkResult,
      ),

    clearSortOrder: (driveId: string): Promise<void> =>
      call(deleteSortOrderExtrasSortOrderDriveIdDelete, { path: { drive_id: driveId } }),

    getPreferences: (): Promise<Preferences> =>
      call(readPreferencesExtrasPreferencesGet, {}, preferences),

    setPreferences: (payload: PreferencesInput): Promise<Preferences> =>
      call(
        writePreferencesExtrasPreferencesPut,
        {
          body: {
            show_non_doc_files: payload.showNonDocFiles ?? null,
            hidden_workspace_ids: payload.hiddenWorkspaceIds ?? null,
          },
        },
        preferences,
      ),

    getMe: (): Promise<UserProfile> => call(meAuthMeGet, {}, userProfile),

    getBootstrap: (): Promise<Bootstrap> => call(bootstrapBootstrapGet, {}, bootstrapRead),

    driveSearch: (query: string, limitPerDrive?: number): Promise<DriveSearchResult[]> =>
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

    driveSearchSnippet: (
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
  };
}

export const wikiApi = makeWikiApi();
