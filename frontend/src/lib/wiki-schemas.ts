import { type z } from "zod";

import {
  zBootstrapRead,
  zDocIconRead,
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
    name: n.name,
    mimeType: n.mime_type,
    parentId: n.parent_id ?? undefined,
    modifiedTime: n.modified_time ?? undefined,
    shortcutTargetId: n.shortcut_target_id ?? undefined,
    shortcutTargetMimeType: n.shortcut_target_mime_type ?? undefined,
    shortcutTargetDriveId: n.shortcut_target_drive_id ?? undefined,
    shortcutTargetDriveName: n.shortcut_target_drive_name ?? undefined,
    shortcutInaccessible: n.shortcut_inaccessible,
  };
}

function toFileResponse(f: z.infer<typeof zFileResponse>) {
  return {
    id: f.id,
    name: f.name,
    mimeType: f.mime_type,
    parentFolderId: f.parent_folder_id ?? undefined,
    driveId: f.drive_id ?? undefined,
    modifiedTime: f.modified_time ?? undefined,
  };
}

export const driveSummary = zDriveSummary.transform((d) => ({
  id: d.id,
  name: d.name,
}));

export const driveNode = zDriveNode.transform(toDriveNode);

export const driveTree = zDriveTree.transform((t) => ({
  driveId: t.drive_id,
  folders: t.folders.map(toDriveNode),
  files: t.files.map(toDriveNode),
}));

export const searchResult = zSearchResult.transform((r) => ({
  id: r.id,
  name: r.name,
  mimeType: r.mime_type,
  driveId: r.drive_id ?? undefined,
  driveName: r.drive_name ?? undefined,
  parentId: r.parent_id ?? undefined,
  modifiedTime: r.modified_time ?? undefined,
  shortcutTargetId: r.shortcut_target_id ?? undefined,
  shortcutTargetMimeType: r.shortcut_target_mime_type ?? undefined,
  shortcutTargetDriveName: r.shortcut_target_drive_name ?? undefined,
  shortcutInaccessible: r.shortcut_inaccessible,
}));

export const snippetResult = zSnippetResult.transform((s) => ({
  fileId: s.file_id,
  mimeType: s.mime_type,
  snippet: s.snippet ?? undefined,
  matchTerm: s.match_term ?? undefined,
}));

export const fileResponse = zFileResponse.transform(toFileResponse);

export const fileDetail = zFileDetail.transform((f) => ({
  ...toFileResponse(f),
  driveName: f.drive_name ?? undefined,
  breadcrumb: f.breadcrumb.map((b) => ({ id: b.id, name: b.name })),
}));

export const docIcon = zDocIconRead.transform((d) => ({
  driveId: d.drive_id,
  icon: d.icon ?? undefined,
  coverUrl: d.cover_url ?? undefined,
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
  showNonDocFiles: p.show_non_doc_files,
  hiddenWorkspaceIds: p.hidden_workspace_ids,
}));

export const userProfile = zUserProfile.transform((u) => ({
  id: u.id,
  email: u.email,
  displayName: u.display_name ?? undefined,
  avatarUrl: u.avatar_url ?? undefined,
}));

export const bootstrapRead = zBootstrapRead.transform((b) => ({
  drives: b.drives.map((d) => driveSummary.parse(d)),
  preferences: preferences.parse(b.preferences),
  folderIcons: b.folder_icons.map((f) => folderIcon.parse(f)),
  docIcons: b.doc_icons.map((d) => docIcon.parse(d)),
  sortOrders: b.sort_orders.map((s) => sortOrder.parse(s)),
  me: userProfile.parse(b.me),
}));
