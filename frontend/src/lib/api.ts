import type { Page, PagePermission } from "@/types";
import { refreshAccessToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

function transformKeys<T>(obj: unknown): T {
  if (Array.isArray(obj)) return obj.map(transformKeys) as T;
  if (obj && typeof obj === "object") {
    return Object.fromEntries(
      Object.entries(obj).map(([key, val]) => [
        snakeToCamel(key),
        transformKeys(val),
      ])
    ) as T;
  }
  return obj as T;
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  let res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  // If 401, try refreshing the token and retry once
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      res = await fetch(`${API_URL}${path}`, {
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...options?.headers,
        },
        ...options,
      });
    }
  }

  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }

  // 204 No Content — nothing to parse
  if (res.status === 204) {
    return undefined as T;
  }

  const json = await res.json();
  return transformKeys<T>(json);
}

export const api = {
  // Pages
  getPageTree: () => fetchApi<Page[]>("/pages/tree"),
  syncPages: () => fetchApi<{ removed: number }>("/pages/sync", { method: "POST" }),
  getPage: (id: string) => fetchApi<Page>(`/pages/${id}`),
  createPage: (data: {
    title: string;
    parentId?: string;
    workspaceId?: string;
    type?: string;
    icon?: string;
  }) =>
    fetchApi<Page>("/pages", {
      method: "POST",
      body: JSON.stringify({
        title: data.title,
        parent_id: data.parentId,
        workspace_id: data.workspaceId,
        type: data.type || "page",
        icon: data.icon,
      }),
    }),
  updatePage: (
    id: string,
    data: {
      title?: string;
      parentId?: string | null;
      sortOrder?: number;
      icon?: string | null;
    }
  ) => {
    // Only include fields that were explicitly provided
    const body: Record<string, unknown> = {};
    if (data.title !== undefined) body.title = data.title;
    if ("parentId" in data) body.parent_id = data.parentId;
    if (data.sortOrder !== undefined) body.sort_order = data.sortOrder;
    if ("icon" in data) body.icon = data.icon;
    return fetchApi<Page>(`/pages/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  deletePage: (id: string) =>
    fetchApi<void>(`/pages/${id}`, { method: "DELETE" }),
  publishPage: (id: string, workspaceId: string) =>
    fetchApi<Page>(`/pages/${id}/publish`, {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),

  // Permissions
  getPagePermissions: (pageId: string) =>
    fetchApi<PagePermission[]>(`/permissions/page/${pageId}`),
  grantPermission: (data: {
    pageId: string;
    userId?: string;
    workspaceId?: string;
    permission: string;
  }) =>
    fetchApi<PagePermission>("/permissions", {
      method: "POST",
      body: JSON.stringify({
        page_id: data.pageId,
        user_id: data.userId,
        workspace_id: data.workspaceId,
        permission: data.permission,
      }),
    }),
  revokePermission: (id: string) =>
    fetchApi<void>(`/permissions/${id}`, { method: "DELETE" }),
};
