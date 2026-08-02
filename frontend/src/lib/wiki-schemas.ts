import type { z } from "zod";

import {
  zBootstrapRead,
  zDocIconRead,
  zDocumentAliasForFile,
  zDocumentAliasRead,
  zDriveNode,
  zDriveSummary,
  zDriveTree,
  zFileDetail,
  zFileResponse,
  zFolderIconRead,
  zPreferencesRead,
  zSearchResult,
  zSnippetResult,
  zSortOrderBulkResult,
  zSortOrderRead,
  zUserProfile,
} from "@/lib/generated/zod.gen";

function toDriveNode(n: z.infer<typeof zDriveNode>) {
  return {
    id: n.id,
    mimeType: n.mime_type,
    modifiedTime: n.modified_time ?? undefined,
    name: n.name,
    parentId: n.parent_id ?? undefined,
    shortcutInaccessible: n.shortcut_inaccessible,
    shortcutTargetDriveId: n.shortcut_target_drive_id ?? undefined,
    shortcutTargetDriveName: n.shortcut_target_drive_name ?? undefined,
    shortcutTargetId: n.shortcut_target_id ?? undefined,
    shortcutTargetMimeType: n.shortcut_target_mime_type ?? undefined,
  };
}

function toFileResponse(f: z.infer<typeof zFileResponse>) {
  return {
    canEdit: f.can_edit,
    driveId: f.drive_id ?? undefined,
    id: f.id,
    mimeType: f.mime_type,
    modifiedTime: f.modified_time ?? undefined,
    name: f.name,
    parentFolderId: f.parent_folder_id ?? undefined,
  };
}

export const documentAlias = zDocumentAliasRead.transform((a) => ({
  alias: a.alias,
  fileId: a.file_id,
}));

export const documentAliasForFile = zDocumentAliasForFile.transform((a) => ({
  alias: a.alias ?? undefined,
}));

export const driveSummary = zDriveSummary.transform((d) => ({
  id: d.id,
  isPersonal: d.is_personal,
  name: d.name,
}));

export const driveNode = zDriveNode.transform(toDriveNode);

export const driveTree = zDriveTree.transform((t) => ({
  driveId: t.drive_id,
  files: t.files.map(toDriveNode),
  filesComplete: t.files_complete,
  folders: t.folders.map(toDriveNode),
}));

export const searchResult = zSearchResult.transform((r) => ({
  driveId: r.drive_id ?? undefined,
  driveName: r.drive_name ?? undefined,
  id: r.id,
  mimeType: r.mime_type,
  modifiedTime: r.modified_time ?? undefined,
  name: r.name,
  parentId: r.parent_id ?? undefined,
  shortcutInaccessible: r.shortcut_inaccessible,
  shortcutTargetDriveName: r.shortcut_target_drive_name ?? undefined,
  shortcutTargetId: r.shortcut_target_id ?? undefined,
  shortcutTargetMimeType: r.shortcut_target_mime_type ?? undefined,
}));

export const snippetResult = zSnippetResult.transform((s) => ({
  fileId: s.file_id,
  matchTerm: s.match_term ?? undefined,
  mimeType: s.mime_type,
  snippet: s.snippet ?? undefined,
}));

export const fileResponse = zFileResponse.transform(toFileResponse);

export const fileDetail = zFileDetail.transform((f) => ({
  ...toFileResponse(f),
  breadcrumb: f.breadcrumb.map((b) => ({ id: b.id, name: b.name })),
  driveName: f.drive_name ?? undefined,
}));

export const docIcon = zDocIconRead.transform((d) => ({
  coverUrl: d.cover_url ?? undefined,
  driveId: d.drive_id,
  icon: d.icon ?? undefined,
}));

export const folderIcon = zFolderIconRead.transform((f) => ({
  folderDriveId: f.folder_drive_id,
  icon: f.icon ?? undefined,
}));

export const sortOrder = zSortOrderRead.transform((s) => ({
  driveId: s.drive_id,
  sortOrder: s.sort_order,
}));

export const sortOrderBulkResult = zSortOrderBulkResult.transform((r) => ({
  updated: r.updated,
}));

export const preferences = zPreferencesRead.transform((p) => ({
  hiddenWorkspaceIds: p.hidden_workspace_ids,
  showNonDocFiles: p.show_non_doc_files,
}));

export const userProfile = zUserProfile.transform((u) => ({
  avatarUrl: u.avatar_url ?? undefined,
  displayName: u.display_name ?? undefined,
  email: u.email,
  id: u.id,
}));

export const bootstrapRead = zBootstrapRead.transform((b) => ({
  docIcons: b.doc_icons.map((d) => docIcon.parse(d)),
  drives: b.drives.map((d) => driveSummary.parse(d)),
  firstDriveTree: b.first_drive_tree ? driveTree.parse(b.first_drive_tree) : undefined,
  folderIcons: b.folder_icons.map((f) => folderIcon.parse(f)),
  me: userProfile.parse(b.me),
  preferences: preferences.parse(b.preferences),
  sortOrders: b.sort_orders.map((s) => sortOrder.parse(s)),
}));
