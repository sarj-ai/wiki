"use client";

import {
  getCurrentUserAction,
  getDocumentAliasAction,
  getDriveFoldersAction,
  getDrivePermissionsAction,
  getDrivesAction,
  getDriveSearchSnippetAction,
  getFileAction,
  getFolderFilesAction,
  getPreferencesAction,
  listDocIconsAction,
  listFolderIconsAction,
  listSortOrdersAction,
  searchDriveAction,
} from "@/app/actions/wiki-actions";
import { runAction } from "@/lib/action-client";

/** Browser-safe reads through authenticated Server Actions. */
export const WIKI_READ_API = Object.freeze({
  driveSearch: async (query: string, limitPerDrive?: number) =>
    await runAction(async () => await searchDriveAction(query, limitPerDrive)),
  driveSearchSnippet: async (fileId: string, query: string, mimeType?: string) =>
    await runAction(async () => await getDriveSearchSnippetAction(fileId, query, mimeType)),
  getDocumentAlias: async (fileId: string) =>
    await runAction(async () => await getDocumentAliasAction(fileId)),
  getDriveFolders: async (driveId: string) =>
    await runAction(async () => await getDriveFoldersAction(driveId)),
  getDrivePermissions: async (driveId: string, pageToken?: string) =>
    await runAction(async () => await getDrivePermissionsAction(driveId, pageToken)),
  getDrives: async () => await runAction(getDrivesAction),
  getFile: async (fileId: string) => await runAction(async () => await getFileAction(fileId)),
  getFolderFiles: async (folderId: string) =>
    await runAction(async () => await getFolderFilesAction(folderId)),
  getMe: async () => await runAction(getCurrentUserAction),
  getPreferences: async () => await runAction(getPreferencesAction),
  listDocIcons: async () => await runAction(listDocIconsAction),
  listFolderIcons: async () => await runAction(listFolderIconsAction),
  listSortOrders: async () => await runAction(listSortOrdersAction),
});
