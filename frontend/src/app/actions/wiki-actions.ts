"use server";

import { cookies } from "next/headers";

import type { ActionResult } from "@/lib/action-result";
import { ServerApiError, serverWikiApi } from "@/lib/server-api";
import type { CreateFileInput, DocIconInput, PreferencesInput, UserProfile } from "@/lib/wiki-api";
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

export async function getCurrentUserAction(): Promise<ActionResult<UserProfile>> {
  return await resultOf(async () => await serverWikiApi.getMe());
}

export async function getDrivesAction(): Promise<ActionResult<DriveSummary[]>> {
  return await resultOf(async () => await serverWikiApi.getDrives());
}

export async function getDriveFoldersAction(driveId: string): Promise<ActionResult<DriveTree>> {
  return await resultOf(async () => await serverWikiApi.getDriveFolders(driveId));
}

export async function getDrivePermissionsAction(
  driveId: string,
  pageToken?: string,
): Promise<ActionResult<DrivePermissionsPage>> {
  return await resultOf(async () => await serverWikiApi.getDrivePermissions(driveId, pageToken));
}

export async function getFileAction(fileId: string): Promise<ActionResult<FileDetail>> {
  return await resultOf(async () => await serverWikiApi.getFile(fileId));
}

export async function getFolderFilesAction(folderId: string): Promise<ActionResult<DriveNode[]>> {
  return await resultOf(async () => await serverWikiApi.getFolderFiles(folderId));
}

export async function getPreferencesAction(): Promise<ActionResult<Preferences>> {
  return await resultOf(async () => await serverWikiApi.getPreferences());
}

export async function listFolderIconsAction(): Promise<ActionResult<FolderIcon[]>> {
  return await resultOf(async () => await serverWikiApi.listFolderIcons());
}

export async function listDocIconsAction(): Promise<ActionResult<DocIcon[]>> {
  return await resultOf(async () => await serverWikiApi.listDocIcons());
}

export async function listSortOrdersAction(): Promise<ActionResult<SortOrder[]>> {
  return await resultOf(async () => await serverWikiApi.listSortOrders());
}

export async function searchDriveAction(
  query: string,
  limitPerDrive?: number,
): Promise<ActionResult<DriveSearchResult[]>> {
  return await resultOf(async () => await serverWikiApi.driveSearch(query, limitPerDrive));
}

export async function getDriveSearchSnippetAction(
  fileId: string,
  query: string,
  mimeType?: string,
): Promise<ActionResult<SnippetResult>> {
  return await resultOf(
    async () => await serverWikiApi.driveSearchSnippet(fileId, query, mimeType),
  );
}

export async function getDocumentAliasAction(
  fileId: string,
): Promise<ActionResult<DocumentAliasForFile>> {
  return await resultOf(async () => await serverWikiApi.getDocumentAlias(fileId));
}

export async function logoutAction(): Promise<ActionResult<null>> {
  return resultOf(async () => {
    try {
      await serverWikiApi.logout();
    } finally {
      const cookieStore = await cookies();
      for (const name of ["access_token", "refresh_token", "session_exp"]) {
        cookieStore.delete(name);
      }
    }
    return null;
  });
}

export async function createFileAction(
  input: CreateFileInput,
): Promise<ActionResult<FileResponse>> {
  return await resultOf(async () => await serverWikiApi.createFile(input));
}

export async function renameFileAction(
  fileId: string,
  name: string,
): Promise<ActionResult<FileResponse>> {
  return await resultOf(async () => await serverWikiApi.renameFile(fileId, name));
}

export async function moveFileAction(
  fileId: string,
  newParentFolderId: string,
): Promise<ActionResult<FileResponse>> {
  return await resultOf(async () => await serverWikiApi.moveFile(fileId, newParentFolderId));
}

export async function copyFileAction(
  fileId: string,
  name: string,
): Promise<ActionResult<FileResponse>> {
  return await resultOf(async () => await serverWikiApi.copyFile(fileId, name));
}

export async function deleteFileAction(fileId: string): Promise<ActionResult<null>> {
  return resultOf(async () => {
    await serverWikiApi.deleteFile(fileId);
    return null;
  });
}

export async function refreshDriveAction(driveId: string): Promise<ActionResult<DriveTree>> {
  return await resultOf(async () => await serverWikiApi.refreshDriveFolders(driveId));
}

export async function bulkSetSortOrdersAction(
  items: SortOrder[],
): Promise<ActionResult<{ updated: number }>> {
  return await resultOf(async () => await serverWikiApi.bulkSetSortOrders(items));
}

export async function setPreferencesAction(
  input: PreferencesInput,
): Promise<ActionResult<Preferences>> {
  return await resultOf(async () => await serverWikiApi.setPreferences(input));
}

export async function setDocIconAction(
  driveId: string,
  input: DocIconInput,
): Promise<ActionResult<DocIcon>> {
  return await resultOf(async () => await serverWikiApi.setDocIcon(driveId, input));
}

export async function setFolderIconAction(
  folderDriveId: string,
  icon: null | string,
): Promise<ActionResult<FolderIcon>> {
  return await resultOf(async () => await serverWikiApi.setFolderIcon(folderDriveId, icon));
}

export async function saveDocumentAliasAction(
  fileId: string,
  alias: string,
): Promise<ActionResult<DocumentAlias>> {
  return await resultOf(async () => await serverWikiApi.saveDocumentAlias(fileId, alias));
}

export async function retireDocumentAliasAction(fileId: string): Promise<ActionResult<null>> {
  return resultOf(async () => {
    await serverWikiApi.retireDocumentAlias(fileId);
    return null;
  });
}

async function resultOf<T>(work: () => Promise<T>): Promise<ActionResult<T>> {
  try {
    return { data: await work(), ok: true };
  } catch (error: unknown) {
    const status = error instanceof ServerApiError ? error.status : 500;
    return {
      error: status === 401 ? "Your session has expired" : "The request could not be completed",
      ok: false,
      status,
    };
  }
}
