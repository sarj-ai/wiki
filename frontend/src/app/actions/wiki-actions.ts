"use server";

import { cookies } from "next/headers";

import type { ActionResult } from "@/lib/action-result";
import { getRecentDocumentAliases, type RecentDocumentAlias } from "@/lib/alias-client";
import { RATE_LIMIT_MESSAGE } from "@/lib/api-error";
import { zRecentDocumentAliasesRequest } from "@/lib/generated/zod.gen";
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

export async function getRecentDocumentAliasesAction(
  aliases: string[],
): Promise<ActionResult<RecentDocumentAlias[]>> {
  const payload = zRecentDocumentAliasesRequest.safeParse({ aliases });
  if (!payload.success) return { error: "Invalid recent aliases", ok: false, status: 400 };
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) return { error: "Your session has expired", ok: false, status: 401 };
  const result = await getRecentDocumentAliases(token, payload.data.aliases);
  if (result.status === "found") return { data: result.aliases, ok: true };
  if (result.status === "unauthorized") {
    return { error: "Your session has expired", ok: false, status: 401 };
  }
  return { error: "Quick links could not be validated", ok: false, status: 502 };
}

// react-doctor-disable-next-line server-auth-actions -- The backend logout call authenticates the caller before this action clears only that caller's cookies.
export async function logoutAction(): Promise<ActionResult<null>> {
  return resultOf(async () => {
    try {
      await serverWikiApi.logout();
    } finally {
      await clearSessionCookies();
    }
    return null;
  });
}

async function clearSessionCookies(): Promise<void> {
  const cookieStore = await cookies();
  for (const name of ["access_token", "refresh_token", "session_exp"]) {
    cookieStore.delete(name);
  }
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

export async function ensureDocumentAliasAction(
  fileId: string,
): Promise<ActionResult<DocumentAlias>> {
  return await resultOf(async () => await serverWikiApi.ensureDocumentAlias(fileId));
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
      error:
        status === 401
          ? "Your session has expired"
          : status === 429
            ? RATE_LIMIT_MESSAGE
            : "The request could not be completed",
      ok: false,
      status,
    };
  }
}
