import { captureBrowserSession } from "@/lib/browser-session";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";

const REFRESH_TIMEOUT_MS = 10_000;

export type SessionRefreshResult = "refreshed" | "unauthorized" | "unavailable";

const REFRESHES = new WeakMap<AbortSignal, Promise<SessionRefreshResult>>();

export async function deduplicatedRefresh(
  signal?: AbortSignal,
  session = captureBrowserSession(),
): Promise<SessionRefreshResult> {
  session.throwIfAborted();
  signal?.throwIfAborted();
  let pendingRefresh = REFRESHES.get(session);
  if (!pendingRefresh) {
    pendingRefresh = rawRefresh(session).finally(() => {
      REFRESHES.delete(session);
    });
    REFRESHES.set(session, pendingRefresh);
  }
  // A discarded read can stop waiting without cancelling the shared refresh
  // still needed by this session's other reads and background session timer.
  const shared = pendingRefresh;
  const signals = signal === undefined || signal === session ? [session] : [session, signal];
  return await new Promise<SessionRefreshResult>((resolve, reject) => {
    const onAbort = () => {
      const reason: unknown = signals.find((value) => value.aborted)?.reason;
      reject(
        reason instanceof Error
          ? reason
          : new DOMException("The operation was aborted", "AbortError"),
      );
    };
    for (const value of signals) value.addEventListener("abort", onAbort, { once: true });
    if (signals.some((value) => value.aborted)) onAbort();
    void (async () => {
      try {
        const result = await shared;
        for (const value of signals) value.throwIfAborted();
        resolve(result);
      } finally {
        for (const value of signals) value.removeEventListener("abort", onAbort);
      }
    })().catch(reject);
  });
}

async function rawRefresh(session: AbortSignal): Promise<SessionRefreshResult> {
  try {
    const response = await fetch(`${API_URL}/auth/refresh`, {
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      method: "POST",
      signal: AbortSignal.any([session, AbortSignal.timeout(REFRESH_TIMEOUT_MS)]),
    });
    discardStream(response.body);
    session.throwIfAborted();
    if (response.ok) return "refreshed";
    return response.status === 401 ? "unauthorized" : "unavailable";
  } catch {
    return "unavailable";
  }
}
