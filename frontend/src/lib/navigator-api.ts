import { refreshAccessToken } from "@/lib/auth";
import { sanitizeRedirectPath } from "@/lib/url-utils";
import type {
  DocIcon,
  DriveNode,
  DriveSearchResult,
  DriveSummary,
  DriveTree,
  FileResponse,
  FolderIcon,
  Preferences,
  SnippetResult,
  SortOrder,
} from "@/types/navigator";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const REDIRECT_KEY = "sarj-redirect";

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
      Object.entries(obj).map(([key, val]) => [snakeToCamel(key), transformKeys(val)]),
    ) as T;
  }
  return obj as T;
}

function buildRequestInit(options?: RequestInit): RequestInit {
  const { headers: extraHeaders, ...rest } = options ?? {};
  const headers = new Headers({
    "Content-Type": "application/json",
    ...(extraHeaders instanceof Headers
      ? Object.fromEntries(extraHeaders.entries())
      : Array.isArray(extraHeaders)
        ? Object.fromEntries(extraHeaders)
        : extraHeaders),
  });
  return {
    credentials: "include",
    headers,
    ...rest,
  };
}

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname === "/login") return;
  const safePath = sanitizeRedirectPath(window.location.pathname);
  localStorage.setItem(REDIRECT_KEY, safePath);
  window.location.href = `/login?redirect=${encodeURIComponent(safePath)}`;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  let res = await fetch(`${API_URL}${path}`, buildRequestInit(options));

  if (res.status === 401) {
    const refreshed = await deduplicatedRefresh();
    if (refreshed) {
      res = await fetch(`${API_URL}${path}`, buildRequestInit(options));
    }
    if (res.status === 401) {
      redirectToLogin();
      throw new ApiError(401, "Session expired");
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, `API error: ${res.status}`);
  }

  if (res.status === 204) {
    return undefined as unknown as T;
  }

  const json = await res.json();
  return transformKeys<T>(json);
}

export interface CreateFileInput {
  parentFolderId: string;
  name: string;
  mimeType: string;
}

export interface DocIconInput {
  icon?: string | null;
  coverUrl?: string | null;
}

export interface PreferencesInput {
  showNonDocFiles?: boolean;
  hiddenWorkspaceIds?: string[];
}

export const navigatorApi = {
  // ---- Sidebar ---------------------------------------------------------
  getDrives: () => fetchApi<DriveSummary[]>("/sidebar/drives"),

  getDriveFolders: (driveId: string) =>
    fetchApi<DriveTree>(`/sidebar/drives/${encodeURIComponent(driveId)}/folders`),

  getFolderFiles: (folderId: string) =>
    fetchApi<DriveNode[]>(`/sidebar/folders/${encodeURIComponent(folderId)}/files`),

  // ---- Files -----------------------------------------------------------
  getFile: (fileId: string) => fetchApi<FileResponse>(`/files/${encodeURIComponent(fileId)}`),

  createFile: (data: CreateFileInput) =>
    fetchApi<FileResponse>("/files", {
      method: "POST",
      body: JSON.stringify({
        parent_folder_id: data.parentFolderId,
        name: data.name,
        mime_type: data.mimeType,
      }),
    }),

  renameFile: (fileId: string, name: string) =>
    fetchApi<FileResponse>(`/files/${encodeURIComponent(fileId)}/rename`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  moveFile: (fileId: string, newParentFolderId: string) =>
    fetchApi<FileResponse>(`/files/${encodeURIComponent(fileId)}/move`, {
      method: "PATCH",
      body: JSON.stringify({ new_parent_folder_id: newParentFolderId }),
    }),

  deleteFile: (fileId: string) =>
    fetchApi<void>(`/files/${encodeURIComponent(fileId)}`, { method: "DELETE" }),

  // ---- Extras: doc icons (global) --------------------------------------
  listDocIcons: () => fetchApi<DocIcon[]>("/extras/doc-icons"),

  getDocIcon: (driveId: string) =>
    fetchApi<DocIcon>(`/extras/doc-icons/${encodeURIComponent(driveId)}`),

  setDocIcon: (driveId: string, payload: DocIconInput) =>
    fetchApi<DocIcon>(`/extras/doc-icons/${encodeURIComponent(driveId)}`, {
      method: "PUT",
      body: JSON.stringify({
        icon: payload.icon ?? null,
        cover_url: payload.coverUrl ?? null,
      }),
    }),

  // ---- Extras: folder icons (per-user) ---------------------------------
  listFolderIcons: () => fetchApi<FolderIcon[]>("/extras/folder-icons"),

  setFolderIcon: (folderDriveId: string, icon: string | null) =>
    fetchApi<FolderIcon>(`/extras/folder-icons/${encodeURIComponent(folderDriveId)}`, {
      method: "PUT",
      body: JSON.stringify({ icon }),
    }),

  // ---- Extras: sort order (per-user) -----------------------------------
  listSortOrders: () => fetchApi<SortOrder[]>("/extras/sort-order"),

  bulkSetSortOrders: (items: SortOrder[]) =>
    fetchApi<{ updated: number }>("/extras/sort-order", {
      method: "PUT",
      body: JSON.stringify({
        items: items.map((item) => ({
          drive_id: item.driveId,
          sort_order: item.sortOrder,
        })),
      }),
    }),

  clearSortOrder: (driveId: string) =>
    fetchApi<void>(`/extras/sort-order/${encodeURIComponent(driveId)}`, {
      method: "DELETE",
    }),

  // ---- Extras: preferences (per-user) ----------------------------------
  getPreferences: () => fetchApi<Preferences>("/extras/preferences"),

  setPreferences: (payload: PreferencesInput) => {
    const body: Record<string, unknown> = {};
    if (payload.showNonDocFiles !== undefined) body.show_non_doc_files = payload.showNonDocFiles;
    if (payload.hiddenWorkspaceIds !== undefined)
      body.hidden_workspace_ids = payload.hiddenWorkspaceIds;
    return fetchApi<Preferences>("/extras/preferences", {
      method: "PUT",
      body: JSON.stringify(body),
    });
  },

  // ---- Drive Search ----------------------------------------------------
  driveSearch: (query: string, limitPerDrive?: number) => {
    const params = new URLSearchParams({ q: query });
    if (limitPerDrive !== undefined) params.set("limit_per_drive", String(limitPerDrive));
    return fetchApi<DriveSearchResult[]>(`/drive-search?${params.toString()}`);
  },

  driveSearchSnippet: (fileId: string, query: string) => {
    const params = new URLSearchParams({ file_id: fileId, q: query });
    return fetchApi<SnippetResult>(`/drive-search/snippet?${params.toString()}`);
  },
};
