import { API_URL } from "@/lib/env";

const REFRESH_TIMEOUT_MS = 10_000;

export type SessionRefreshResult = "refreshed" | "unauthorized" | "unavailable";

let refreshPromise: null | Promise<SessionRefreshResult> = null;

export async function deduplicatedRefresh(signal?: AbortSignal): Promise<SessionRefreshResult> {
  signal?.throwIfAborted();
  refreshPromise ??= rawRefresh().finally(() => {
    refreshPromise = null;
  });
  if (!signal) return await refreshPromise;
  // A discarded read can stop waiting without cancelling the shared refresh
  // still needed by other reads and the background session timer.
  const pendingRefresh = refreshPromise;
  return await new Promise<SessionRefreshResult>((resolve, reject) => {
    const onAbort = () => {
      const reason: unknown = signal.reason;
      reject(
        reason instanceof Error
          ? reason
          : new DOMException("The operation was aborted", "AbortError"),
      );
    };
    signal.addEventListener("abort", onAbort, { once: true });
    void (async () => {
      try {
        resolve(await pendingRefresh);
      } finally {
        signal.removeEventListener("abort", onAbort);
      }
    })().catch(reject);
  });
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
