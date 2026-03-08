export interface Page {
  id: string;
  parentId: string | null;
  googleDriveId: string;
  title: string;
  type: "page" | "section";
  icon: string | null;
  isPrivate: boolean;
  workspaceId: string | null;
  sortOrder: number;
  children: Page[];
}

export interface Workspace {
  id: string;
  name: string;
  googleGroupEmail: string;
}

export interface User {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export type Permission = "view" | "edit";

export interface PagePermission {
  id: string;
  pageId: string;
  userId: string | null;
  workspaceId: string | null;
  permission: Permission;
  // Denormalized for display
  displayName: string;
  email?: string;
}
