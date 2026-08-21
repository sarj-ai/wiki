import { API_URL } from "@/lib/env";

const REFRESH_TIMEOUT_MS = 10_000;

export type SessionRefreshResult = "refreshed" | "unauthorized" | "unavailable";

let refreshPromise: null | Promise<SessionRefreshResult> = null;

export async function deduplicatedRefresh(): Promise<SessionRefreshResult> {
  refreshPromise ??= rawRefresh().finally(() => {
    refreshPromise = null;
  });
  return await refreshPromise;
}

async function rawRefresh(): Promise<SessionRefreshResult> {
  try {
    const response = await fetch(`${API_URL}/auth/refresh`, {
      credentials: "include",
      method: "POST",
      signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
    });
    if (response.ok) return "refreshed";
    return response.status === 401 ? "unauthorized" : "unavailable";
  } catch {
    return "unavailable";
  }
}
