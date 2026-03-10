import type { Page, PagePermission, ContentBlock } from "@/types";
import { refreshAccessToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

function buildRequestInit(options?: RequestInit): RequestInit {
  return {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  };
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  let res = await fetch(`${API_URL}${path}`, buildRequestInit(options));

  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      res = await fetch(`${API_URL}${path}`, buildRequestInit(options));
    }
  }

  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const json = await res.json();
  return transformKeys<T>(json);
}

function pagePath(id: string): string {
  return `/pages/${encodeURIComponent(id)}`;
}

interface CreatePageData {
  title: string;
  parentId?: string;
  workspaceId?: string;
  type?: string;
  icon?: string;
}

interface UpdatePageData {
  title?: string;
  parentId?: string | null;
  sortOrder?: number;
  icon?: string | null;
}

interface GrantPermissionData {
  pageId: string;
  userId?: string;
  workspaceId?: string;
  permission: string;
}

interface PageContent {
  blocks: ContentBlock[];
  cached: boolean;
}

export const api = {
  getPageTree: () => fetchApi<Page[]>("/pages/tree"),

  syncPages: () => fetchApi<{ removed: number; added: number; updated: number }>("/pages/sync", { method: "POST" }),

  getPage: (id: string) => fetchApi<Page>(pagePath(id)),

  createPage: (data: CreatePageData) =>
    fetchApi<Page>("/pages", {
      method: "POST",
      body: JSON.stringify({
        title: data.title,
        parent_id: data.parentId,
        workspace_id: data.workspaceId,
        type: data.type ?? "page",
        icon: data.icon,
      }),
    }),

  updatePage: (id: string, data: UpdatePageData) => {
    const body: Record<string, unknown> = {};
    if (data.title !== undefined) body.title = data.title;
    if ("parentId" in data) body.parent_id = data.parentId;
    if (data.sortOrder !== undefined) body.sort_order = data.sortOrder;
    if ("icon" in data) body.icon = data.icon;
    return fetchApi<Page>(pagePath(id), {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deletePage: (id: string) =>
    fetchApi<void>(pagePath(id), { method: "DELETE" }),

  publishPage: (id: string, workspaceId: string) =>
    fetchApi<Page>(`${pagePath(id)}/publish`, {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),

  getPageContent: (id: string, forceRefresh?: boolean) =>
    fetchApi<PageContent>(
      `${pagePath(id)}/content${forceRefresh ? "?force_refresh=true" : ""}`
    ),

  getPagePermissions: (pageId: string) =>
    fetchApi<PagePermission[]>(`/permissions/page/${encodeURIComponent(pageId)}`),

  grantPermission: (data: GrantPermissionData) =>
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
    fetchApi<void>(`/permissions/${encodeURIComponent(id)}`, { method: "DELETE" }),

  search: (query: string, limit?: number) =>
    fetchApi<Array<{
      id: string;
      title: string;
      icon: string | null;
      workspaceId: string | null;
      isPrivate: boolean;
      parentId: string | null;
      googleDriveId: string;
      snippet: string | null;
      rank: number;
    }>>(`/search?q=${encodeURIComponent(query)}${limit ? `&limit=${limit}` : ""}`),
};
