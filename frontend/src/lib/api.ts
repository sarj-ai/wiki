import type { Page, PagePermission, Permission, ContentBlock, Workspace, SearchResult, UserSearchResult } from "@/types";
import { refreshAccessToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const REDIRECT_KEY = "sarj-redirect";

// Deduplication for concurrent 401 refresh attempts
let refreshPromise: Promise<boolean> | null = null;

function deduplicatedRefresh(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = refreshAccessToken().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

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
  const { headers: extraHeaders, ...rest } = options ?? {};
  return {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(extraHeaders instanceof Headers
        ? Object.fromEntries(extraHeaders.entries())
        : Array.isArray(extraHeaders)
          ? Object.fromEntries(extraHeaders)
          : extraHeaders),
    },
    ...rest,
  };
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  let res = await fetch(`${API_URL}${path}`, buildRequestInit(options));

  if (res.status === 401) {
    const refreshed = await deduplicatedRefresh();
    if (refreshed) {
      res = await fetch(`${API_URL}${path}`, buildRequestInit(options));
    }
    if (res.status === 401) {
      // Token expired and refresh failed — force login
      if (typeof window !== "undefined" && window.location.pathname !== "/login") {
        localStorage.setItem(REDIRECT_KEY, window.location.pathname);
        window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`;
      }
      throw new Error("Session expired");
    }
  }

  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }

  if (res.status === 204) {
    return undefined as unknown as T;
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
  type?: "page" | "section";
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
  permission: Permission;
}

interface PageContent {
  blocks: ContentBlock[];
  cached: boolean;
}

export const api = {
  getWorkspaces: () => fetchApi<Workspace[]>("/workspaces"),

  syncWorkspaces: () => fetchApi<Workspace[]>("/workspaces/sync", { method: "POST" }),

  updateWorkspace: (id: string, data: { icon?: string | null }) =>
    fetchApi<Workspace>(`/workspaces/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  reorderWorkspaces: (workspaceIds: string[]) =>
    fetchApi<Workspace[]>("/workspaces/reorder", {
      method: "PUT",
      body: JSON.stringify({ workspace_ids: workspaceIds }),
    }),

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

  retryPage: (id: string) =>
    fetchApi<Page>(`${pagePath(id)}/retry`, { method: "POST" }),

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
    fetchApi<SearchResult[]>(`/search?q=${encodeURIComponent(query)}${limit ? `&limit=${limit}` : ""}`),

  searchUsers: (query: string) =>
    fetchApi<UserSearchResult[]>(`/permissions/users/search?q=${encodeURIComponent(query)}`),
};
