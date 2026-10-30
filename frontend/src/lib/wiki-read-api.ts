"use client";

import { z } from "zod";

import { wikiReadHttp } from "@/lib/clients/wiki-read-http";
import {
  docIcon,
  documentAliasForFile,
  driveNode,
  drivePermissionsPage,
  driveSummary,
  driveTree,
  fileDetail,
  folderIcon,
  preferences,
  RecentFileSchema,
  searchResult,
  snippetResult,
  sortOrder,
  userProfile,
} from "@/lib/wiki-schemas";

const DriveSummariesSchema = z.array(driveSummary);
const DriveNodesSchema = z.array(driveNode);
const DocIconsSchema = z.array(docIcon);
const FolderIconsSchema = z.array(folderIcon);
const SortOrdersSchema = z.array(sortOrder);
const SearchResultsSchema = z.array(searchResult);
const RecentFilesSchema = z
  .object({ pages: z.array(RecentFileSchema).max(5) })
  .transform((response) => response.pages);
const QUERY_FIELDS = {
  fileId: "file_id",
  limitPerDrive: "limit_per_drive",
  mimeType: "mime_type",
  pageToken: "page_token",
} as const;

/** Concurrent reads through a restricted, authenticated same-origin route. */
export const WIKI_READ_API = Object.freeze({
  driveSearch: async (query: string, limitPerDrive?: number, signal?: AbortSignal) =>
    await wikiReadHttp(
      "/drive-search",
      SearchResultsSchema,
      { q: query, [QUERY_FIELDS.limitPerDrive]: limitPerDrive },
      signal,
    ),
  driveSearchSnippet: async (
    fileId: string,
    query: string,
    mimeType?: string,
    signal?: AbortSignal,
  ) =>
    await wikiReadHttp(
      "/drive-search/snippet",
      snippetResult,
      { [QUERY_FIELDS.fileId]: fileId, q: query, [QUERY_FIELDS.mimeType]: mimeType },
      signal,
    ),
  getDocumentAlias: async (fileId: string, signal?: AbortSignal) =>
    await wikiReadHttp(
      `/aliases/files/${encodeURIComponent(fileId)}`,
      documentAliasForFile,
      {},
      signal,
    ),
  getDriveFolders: async (driveId: string, signal?: AbortSignal) =>
    await wikiReadHttp(
      `/sidebar/drives/${encodeURIComponent(driveId)}/folders`,
      driveTree,
      {},
      signal,
    ),
  getDrivePermissions: async (driveId: string, pageToken?: string, signal?: AbortSignal) =>
    await wikiReadHttp(
      `/sidebar/drives/${encodeURIComponent(driveId)}/permissions`,
      drivePermissionsPage,
      { [QUERY_FIELDS.pageToken]: pageToken },
      signal,
    ),
  getDrives: async (signal?: AbortSignal) =>
    await wikiReadHttp("/sidebar/drives", DriveSummariesSchema, {}, signal),
  getFile: async (fileId: string, signal?: AbortSignal) =>
    await wikiReadHttp(`/files/${encodeURIComponent(fileId)}`, fileDetail, {}, signal),
  getFolderFiles: async (folderId: string, signal?: AbortSignal) =>
    await wikiReadHttp(
      `/sidebar/folders/${encodeURIComponent(folderId)}/files`,
      DriveNodesSchema,
      {},
      signal,
    ),
  getMe: async (signal?: AbortSignal) => await wikiReadHttp("/auth/me", userProfile, {}, signal),
  getPreferences: async (signal?: AbortSignal) =>
    await wikiReadHttp("/extras/preferences", preferences, {}, signal),
  getRecentFiles: async (fileIds: string[], signal?: AbortSignal) => {
    const params = new URLSearchParams(fileIds.map((id) => ["file_ids", id]));
    return await wikiReadHttp(`/files/recent?${params.toString()}`, RecentFilesSchema, {}, signal);
  },
  listDocIcons: async (signal?: AbortSignal) =>
    await wikiReadHttp("/extras/doc-icons", DocIconsSchema, {}, signal),
  listFolderIcons: async (signal?: AbortSignal) =>
    await wikiReadHttp("/extras/folder-icons", FolderIconsSchema, {}, signal),
  listSortOrders: async (signal?: AbortSignal) =>
    await wikiReadHttp("/extras/sort-order", SortOrdersSchema, {}, signal),
});
