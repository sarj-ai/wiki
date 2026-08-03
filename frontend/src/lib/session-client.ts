import { API_URL } from "@/lib/env";

const REFRESH_TIMEOUT_MS = 10_000;

let refreshPromise: null | Promise<boolean> = null;

export async function deduplicatedRefresh(): Promise<boolean> {
  refreshPromise ??= rawRefresh().finally(() => {
    refreshPromise = null;
  });
  return await refreshPromise;
}

async function rawRefresh(): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}/auth/refresh`, {
      credentials: "include",
      method: "POST",
      signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
    });
    return response.ok;
  } catch {
    return false;
  }
}
