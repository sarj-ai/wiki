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
    pendingRefresh = waitForRefresh(rawRefresh(session), session).finally(() => {
      REFRESHES.delete(session);
    });
    REFRESHES.set(session, pendingRefresh);
  }
  // A discarded read can stop waiting without cancelling the shared refresh
  // still needed by this session's other reads and background session timer.
  const result =
    signal === undefined || signal === session
      ? await pendingRefresh
      : await waitForRefresh(pendingRefresh, signal);
  session.throwIfAborted();
  signal?.throwIfAborted();
  return result;
}

function waitForRefresh(
  shared: Promise<SessionRefreshResult>,
  signal: AbortSignal,
): Promise<SessionRefreshResult> {
  return new Promise<SessionRefreshResult>((resolve, reject) => {
    let listening = true;
    const cleanup = () => {
      if (!listening) return;
      listening = false;
      signal.removeEventListener("abort", onAbort);
    };
    const onAbort = () => {
      cleanup();
      const reason: unknown = signal.reason;
      reject(
        reason instanceof Error
          ? reason
          : new DOMException("The operation was aborted", "AbortError"),
      );
    };
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
    void (async () => {
      try {
        const result = await shared;
        signal.throwIfAborted();
        resolve(result);
      } finally {
        cleanup();
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
