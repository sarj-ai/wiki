import type { z } from "zod";

import type {
  docIcon,
  documentAlias,
  documentAliasForFile,
  driveNode,
  drivePermissionsPage,
  DrivePrincipalTypeSchemaSchema,
  driveSummary,
  driveTree,
  fileDetail,
  fileResponse,
  folderIcon,
  preferences,
  searchResult,
  snippetResult,
  sortOrder,
} from "@/lib/wiki-schemas";

export const MIME_FOLDER = "application/vnd.google-apps.folder";
export const MIME_DOC = "application/vnd.google-apps.document";
export const MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const MIME_SHEET = "application/vnd.google-apps.spreadsheet";
export const MIME_SLIDE = "application/vnd.google-apps.presentation";
export const MIME_DRAWING = "application/vnd.google-apps.drawing";
export const MIME_FORM = "application/vnd.google-apps.form";
export const MIME_PDF = "application/pdf";
export const MIME_SHORTCUT = "application/vnd.google-apps.shortcut";
export const MIME_HTML = "text/html";
export const MIME_MARKDOWN = "text/markdown";
export const MIME_PPTX =
  "application/vnd.openxmlformats-officedocument.presentationml.presentation";

export type DriveSummary = z.output<typeof driveSummary>;
export type DriveNode = z.output<typeof driveNode>;
export type DrivePermissionsPage = z.output<typeof drivePermissionsPage>;
export type DrivePermission = DrivePermissionsPage["permissions"][number];
export type DrivePrincipalType = z.output<typeof DrivePrincipalTypeSchemaSchema>;
export type DriveTree = z.output<typeof driveTree>;
export type DriveSearchResult = z.output<typeof searchResult>;
export type SnippetResult = z.output<typeof snippetResult>;
export type FileResponse = z.output<typeof fileResponse>;
export type FileDetail = z.output<typeof fileDetail>;
export type BreadcrumbItem = FileDetail["breadcrumb"][number];
export type DocIcon = z.output<typeof docIcon>;
export type FolderIcon = z.output<typeof folderIcon>;
export type SortOrder = z.output<typeof sortOrder>;
export type Preferences = z.output<typeof preferences>;
export type DocumentAlias = z.output<typeof documentAlias>;
export type DocumentAliasForFile = z.output<typeof documentAliasForFile>;
