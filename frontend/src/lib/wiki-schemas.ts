import { z } from "zod";

import {
  zBootstrapRead,
  zDocIconRead,
  zDocumentAliasForFile,
  zDocumentAliasRead,
  zDriveNode,
  zDrivePermissionsPage,
  zDriveSummary,
  zDriveTree,
  zFileDetail,
  zFileResponse,
  zFolderIconRead,
  zPreferencesRead,
  zRecentFile,
  zSearchResult,
  zSnippetResult,
  zSortOrderBulkResult,
  zSortOrderRead,
  zUserProfile,
} from "@/lib/generated/zod.gen";

function toDriveNode(n: z.infer<typeof zDriveNode>) {
  return {
    id: n.id,
    mimeType: n["mime_type"],
    modifiedTime: n["modified_time"] ?? undefined,
    name: n.name,
    parentId: n["parent_id"] ?? undefined,
    shortcutInaccessible: n["shortcut_inaccessible"],
    shortcutTargetDriveId: n["shortcut_target_drive_id"] ?? undefined,
    shortcutTargetDriveName: n["shortcut_target_drive_name"] ?? undefined,
    shortcutTargetId: n["shortcut_target_id"] ?? undefined,
    shortcutTargetMimeType: n["shortcut_target_mime_type"] ?? undefined,
  };
}

function toFileResponse(f: z.infer<typeof zFileResponse>) {
  return {
    canEdit: f["can_edit"],
    driveId: f["drive_id"] ?? undefined,
    id: f.id,
    mimeType: f["mime_type"],
    modifiedTime: f["modified_time"] ?? undefined,
    name: f.name,
    parentFolderId: f["parent_folder_id"] ?? undefined,
  };
}

export const DrivePrincipalTypeSchemaSchema = z.enum(["anyone", "domain", "group", "user"]);

export const documentAlias = zDocumentAliasRead.transform((a) => ({
  alias: a.alias,
  fileId: a["file_id"],
}));

export const documentAliasForFile = zDocumentAliasForFile.transform((a) => ({
  alias: a.alias ?? undefined,
}));

function toDriveSummary(d: z.infer<typeof zDriveSummary>) {
  return { id: d.id, isPersonal: d["is_personal"], name: d.name };
}

export const driveSummary = zDriveSummary.transform(toDriveSummary);

export const driveNode = zDriveNode.transform(toDriveNode);

export const drivePermissionsPage = zDrivePermissionsPage.transform((page) => ({
  accessModel: page["access_model"],
  driveId: page["drive_id"],
  nextPageToken: page["next_page_token"] ?? undefined,
  permissions: page.permissions.map((permission) => ({
    allowFileDiscovery: permission["allow_file_discovery"] ?? undefined,
    deleted: permission.deleted,
    displayName: permission["display_name"] ?? undefined,
    domain: permission.domain ?? undefined,
    emailAddress: permission["email_address"] ?? undefined,
    expirationTime: permission["expiration_time"] ?? undefined,
    id: permission.id,
    pendingOwner: permission["pending_owner"],
    principalType: DrivePrincipalTypeSchemaSchema.parse(permission["principal_type"]),
    role: permission.role,
  })),
}));

function toDriveTree(t: z.infer<typeof zDriveTree>) {
  return {
    driveId: t["drive_id"],
    files: t.files.map(toDriveNode),
    filesComplete: t["files_complete"],
    folders: t.folders.map(toDriveNode),
  };
}

export const driveTree = zDriveTree.transform(toDriveTree);

export const searchResult = zSearchResult.transform((r) => ({
  driveId: r["drive_id"] ?? undefined,
  driveName: r["drive_name"] ?? undefined,
  id: r.id,
  mimeType: r["mime_type"],
  modifiedTime: r["modified_time"] ?? undefined,
  name: r.name,
  parentId: r["parent_id"] ?? undefined,
  shortcutInaccessible: r["shortcut_inaccessible"],
  shortcutTargetDriveName: r["shortcut_target_drive_name"] ?? undefined,
  shortcutTargetId: r["shortcut_target_id"] ?? undefined,
  shortcutTargetMimeType: r["shortcut_target_mime_type"] ?? undefined,
}));

export const snippetResult = zSnippetResult.transform((s) => ({
  fileId: s["file_id"],
  matchTerm: s["match_term"] ?? undefined,
  mimeType: s["mime_type"],
  snippet: s.snippet ?? undefined,
}));

export const fileResponse = zFileResponse.transform(toFileResponse);

export const RecentFileSchema = zRecentFile.transform((file) => ({
  driveName: file["drive_name"],
  id: file.id,
  mimeType: file["mime_type"],
  name: file.name,
}));

export const fileDetail = zFileDetail.transform((f) => ({
  ...toFileResponse(f),
  breadcrumb: f.breadcrumb.map((b) => ({ id: b.id, name: b.name })),
  driveName: f["drive_name"] ?? undefined,
}));

function toDocIcon(d: z.infer<typeof zDocIconRead>) {
  return {
    coverUrl: d["cover_url"] ?? undefined,
    driveId: d["drive_id"],
    icon: d.icon ?? undefined,
  };
}

export const docIcon = zDocIconRead.transform(toDocIcon);

function toFolderIcon(f: z.infer<typeof zFolderIconRead>) {
  return { folderDriveId: f["folder_drive_id"], icon: f.icon ?? undefined };
}

export const folderIcon = zFolderIconRead.transform(toFolderIcon);

function toSortOrder(s: z.infer<typeof zSortOrderRead>) {
  return { driveId: s["drive_id"], sortOrder: s["sort_order"] };
}

export const sortOrder = zSortOrderRead.transform(toSortOrder);

export const sortOrderBulkResult = zSortOrderBulkResult.transform((r) => ({
  updated: r.updated,
}));

function toPreferences(p: z.infer<typeof zPreferencesRead>) {
  return {
    hiddenWorkspaceIds: p["hidden_workspace_ids"],
    showNonDocFiles: p["show_non_doc_files"],
  };
}

export const preferences = zPreferencesRead.transform(toPreferences);

function toUserProfile(u: z.infer<typeof zUserProfile>) {
  return {
    avatarUrl: u["avatar_url"] ?? undefined,
    displayName: u["display_name"] ?? undefined,
    email: u.email,
    id: u.id,
  };
}

export const userProfile = zUserProfile.transform(toUserProfile);

export const bootstrapRead = zBootstrapRead.transform((b) => ({
  docIcons: b["doc_icons"].map(toDocIcon),
  drives: b.drives.map(toDriveSummary),
  // zBootstrapRead already validated the nested tree. Only map its keys here.
  firstDriveTree: b["first_drive_tree"] ? toDriveTree(b["first_drive_tree"]) : undefined,
  folderIcons: b["folder_icons"].map(toFolderIcon),
  me: toUserProfile(b.me),
  preferences: toPreferences(b.preferences),
  sortOrders: b["sort_orders"].map(toSortOrder),
}));
