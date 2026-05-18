// Google Workspace mime types Sarj Wiki renders natively.
// Keep in sync with backend/app/drive/schemas.py.
export const MIME_FOLDER = "application/vnd.google-apps.folder";
export const MIME_DOC = "application/vnd.google-apps.document";
export const MIME_SHEET = "application/vnd.google-apps.spreadsheet";
export const MIME_SLIDE = "application/vnd.google-apps.presentation";
export const MIME_DRAWING = "application/vnd.google-apps.drawing";
export const MIME_FORM = "application/vnd.google-apps.form";
export const MIME_PDF = "application/pdf";
// A pointer to a file/folder that lives elsewhere in Drive. Permission
// checks happen against the *target* — the shortcut entry itself is
// inert. The frontend renders shortcuts using the target's mime icon
// plus an overlay glyph and routes navigation through the target id.
export const MIME_SHORTCUT = "application/vnd.google-apps.shortcut";

export interface DriveSummary {
  id: string;
  name: string;
}

export interface DriveNode {
  id: string;
  name: string;
  mimeType: string;
  parentId: string | null;
  modifiedTime: string | null;
  // Shortcut metadata — populated only when ``mimeType === MIME_SHORTCUT``.
  shortcutTargetId?: string | null;
  shortcutTargetMimeType?: string | null;
  shortcutTargetDriveId?: string | null;
  shortcutTargetDriveName?: string | null;
  shortcutInaccessible?: boolean;
}

export interface DriveTree {
  driveId: string;
  folders: DriveNode[];
  files: DriveNode[];
}

export interface FileResponse {
  id: string;
  name: string;
  mimeType: string;
  parentFolderId: string | null;
  driveId: string | null;
  modifiedTime: string | null;
}

export interface BreadcrumbItem {
  id: string;
  name: string;
}

export interface FileDetail extends FileResponse {
  driveName: string | null;
  breadcrumb: BreadcrumbItem[];
}

export interface DocIcon {
  driveId: string;
  icon: string | null;
  coverUrl: string | null;
}

export interface FolderIcon {
  folderDriveId: string;
  icon: string | null;
}

export interface SortOrder {
  driveId: string;
  sortOrder: number;
}

export interface Preferences {
  showNonDocFiles: boolean;
  hiddenWorkspaceIds: string[];
}

export interface DriveSearchResult {
  id: string;
  name: string;
  mimeType: string;
  driveId: string | null;
  driveName: string | null;
  parentId: string | null;
  modifiedTime: string | null;
  shortcutTargetId?: string | null;
  shortcutTargetMimeType?: string | null;
  shortcutTargetDriveName?: string | null;
  shortcutInaccessible?: boolean;
}

export interface SnippetResult {
  fileId: string;
  mimeType: string;
  snippet: string | null;
  matchTerm: string | null;
}
