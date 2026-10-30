import * as z from "zod";

import { makeCall } from "@/lib/api-client";
import type { Client } from "@/lib/generated/client";
import {
  copyFile as copyFileRequest,
  createFile as createFileRequest,
  deleteFile as deleteFileRequest,
  ensureFileAlias,
  getBootstrap as getBootstrapRequest,
  getCurrentUser,
  getDriveTree,
  getFileAlias,
  getSearchSnippet,
  listDocIcons,
  listDrivePermissions,
  listDrives,
  listFolderFiles,
  listFolderIcons,
  listSortOrders,
  logout as logoutRequest,
  moveFile as moveFileRequest,
  readFile,
  readPreferences,
  refreshDriveTree,
  renameFile as renameFileRequest,
  retireFileAlias,
  saveFileAlias,
  searchDrive,
  setDocIcon,
  setFolderIcon,
  setSortOrders,
  writePreferences,
} from "@/lib/generated/sdk.gen";
import {
  bootstrapRead,
  docIcon,
  documentAlias,
  documentAliasForFile,
  driveNode,
  drivePermissionsPage,
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
  DocumentAlias,
  DocumentAliasForFile,
  DriveNode,
  DrivePermissionsPage,
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

const SearchResultsSchema = z.array(searchResult);
const DriveSummariesSchema = z.array(driveSummary);
const DriveNodesSchema = z.array(driveNode);
const DocIconsSchema = z.array(docIcon);
const FolderIconsSchema = z.array(folderIcon);
const SortOrdersSchema = z.array(sortOrder);

export function makeWikiApi(client?: Client) {
  const call = makeCall(client);
  return {
    bulkSetSortOrders: async (items: SortOrder[]): Promise<{ updated: number }> =>
      call(
        setSortOrders,
        {
          body: {
            items: items.map((item) => ({ drive_id: item.driveId, sort_order: item.sortOrder })),
          },
        },
        sortOrderBulkResult,
      ),

    copyFile: async (fileId: string, name: string): Promise<FileResponse> =>
      call(copyFileRequest, { body: { name }, path: { file_id: fileId } }, fileResponse),

    createFile: async (input: CreateFileInput): Promise<FileResponse> =>
      call(
        createFileRequest,
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
      call(deleteFileRequest, { path: { file_id: fileId } }),

    driveSearch: async (query: string, limitPerDrive?: number): Promise<DriveSearchResult[]> =>
      call(
        searchDrive,
        {
          query: {
            q: query,
            ...(!(limitPerDrive === undefined) && { limit_per_drive: limitPerDrive }),
          },
        },
        SearchResultsSchema,
      ),

    driveSearchSnippet: async (
      fileId: string,
      query: string,
      mimeType?: string,
    ): Promise<SnippetResult> =>
      call(
        getSearchSnippet,
        {
          query: {
            file_id: fileId,
            q: query,
            ...(!(mimeType === undefined) && { mime_type: mimeType }),
          },
        },
        snippetResult,
      ),

    getBootstrap: async (signal?: AbortSignal): Promise<Bootstrap> =>
      call(
        getBootstrapRequest,
        { query: { defer_doc_icons: true }, ...optionalSignal(signal) },
        bootstrapRead,
      ),

    getDocumentAlias: async (fileId: string): Promise<DocumentAliasForFile> =>
      call(getFileAlias, { path: { file_id: fileId } }, documentAliasForFile),

    ensureDocumentAlias: async (fileId: string): Promise<DocumentAlias> =>
      call(ensureFileAlias, { path: { file_id: fileId } }, documentAlias),

    getDriveFolders: async (driveId: string): Promise<DriveTree> =>
      call(getDriveTree, { path: { drive_id: driveId } }, driveTree),

    getDrivePermissions: async (
      driveId: string,
      pageToken?: string,
    ): Promise<DrivePermissionsPage> =>
      call(
        listDrivePermissions,
        {
          path: { drive_id: driveId },
          query: pageToken === undefined ? {} : { page_token: pageToken },
        },
        drivePermissionsPage,
      ),

    getDrives: async (): Promise<DriveSummary[]> => call(listDrives, {}, DriveSummariesSchema),

    getFile: async (fileId: string, signal?: AbortSignal): Promise<FileDetail> =>
      call(readFile, { path: { file_id: fileId }, ...optionalSignal(signal) }, fileDetail),

    getFolderFiles: async (folderId: string): Promise<DriveNode[]> =>
      call(listFolderFiles, { path: { folder_id: folderId } }, DriveNodesSchema),

    getMe: async (signal?: AbortSignal): Promise<UserProfile> =>
      call(getCurrentUser, optionalSignal(signal), userProfile),

    getPreferences: async (): Promise<Preferences> => call(readPreferences, {}, preferences),

    listDocIcons: async (): Promise<DocIcon[]> => call(listDocIcons, {}, DocIconsSchema),

    listFolderIcons: async (): Promise<FolderIcon[]> =>
      call(listFolderIcons, {}, FolderIconsSchema),

    listSortOrders: async (): Promise<SortOrder[]> => call(listSortOrders, {}, SortOrdersSchema),

    logout: async (): Promise<void> => call(logoutRequest, {}),

    moveFile: async (fileId: string, newParentFolderId: string): Promise<FileResponse> =>
      call(
        moveFileRequest,
        { body: { new_parent_folder_id: newParentFolderId }, path: { file_id: fileId } },
        fileResponse,
      ),

    refreshDriveFolders: async (driveId: string): Promise<DriveTree> =>
      call(refreshDriveTree, { path: { drive_id: driveId } }, driveTree),

    renameFile: async (fileId: string, name: string): Promise<FileResponse> =>
      call(renameFileRequest, { body: { name }, path: { file_id: fileId } }, fileResponse),

    retireDocumentAlias: async (fileId: string): Promise<void> =>
      call(retireFileAlias, { path: { file_id: fileId } }),

    saveDocumentAlias: async (fileId: string, alias: string): Promise<DocumentAlias> =>
      call(saveFileAlias, { body: { alias }, path: { file_id: fileId } }, documentAlias),

    setDocIcon: async (driveId: string, payload: DocIconInput): Promise<DocIcon> =>
      call(
        setDocIcon,
        {
          body: { cover_url: payload.coverUrl ?? null, icon: payload.icon ?? null },
          path: { drive_id: driveId },
        },
        docIcon,
      ),

    setFolderIcon: async (folderDriveId: string, icon: null | string): Promise<FolderIcon> =>
      call(setFolderIcon, { body: { icon }, path: { folder_drive_id: folderDriveId } }, folderIcon),

    setPreferences: async (payload: PreferencesInput): Promise<Preferences> =>
      call(
        writePreferences,
        {
          body: {
            ...(!(payload.hiddenWorkspaceIds === undefined) && {
              hidden_workspace_ids: payload.hiddenWorkspaceIds,
            }),
            show_non_doc_files: payload.showNonDocFiles ?? null,
          },
        },
        preferences,
      ),
  };
}

export { ApiError } from "@/lib/api-client";

function optionalSignal(signal?: AbortSignal): { signal?: AbortSignal } {
  const options: { signal?: AbortSignal } = {};
  if (signal !== undefined) options.signal = signal;
  return options;
}
