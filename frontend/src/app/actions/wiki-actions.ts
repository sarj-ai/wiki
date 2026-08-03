"use server";

import type { ActionResult } from "@/lib/action-result";
import { ServerApiError, serverWikiApi } from "@/lib/server-api";
import type { CreateFileInput, DocIconInput, PreferencesInput } from "@/lib/wiki-api";
import type {
  DocIcon,
  DocumentAlias,
  DriveTree,
  FileResponse,
  FolderIcon,
  Preferences,
  SortOrder,
} from "@/types/wiki";

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
