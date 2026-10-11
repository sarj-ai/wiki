"use client";

import type { ActionResult } from "@/lib/action-result";
import { ApiError, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { captureBrowserSession } from "@/lib/browser-session";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";

export async function runAction<T>(
  action: () => Promise<ActionResult<T>>,
  signal?: AbortSignal,
): Promise<T> {
  const result = await runActionResult(action, signal);
  return result.data;
}

export async function runActionResult<T>(
  action: () => Promise<ActionResult<T>>,
  signal?: AbortSignal,
): Promise<Extract<ActionResult<T>, { ok: true }>> {
  const session = captureBrowserSession();
  session.throwIfAborted();
  signal?.throwIfAborted();
  const invoke = async () => {
    try {
      return await action();
    } catch (error: unknown) {
      session.throwIfAborted();
      throw error;
    }
  };
  let settled = await invoke();
  session.throwIfAborted();
  signal?.throwIfAborted();
  if (!settled.ok && settled.status === 401) {
    const refresh = await deduplicatedRefresh(signal, session);
    session.throwIfAborted();
    signal?.throwIfAborted();
    if (refresh === "refreshed") settled = await invoke();
    if (refresh === "unavailable") {
      throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    }
  }
  session.throwIfAborted();
  signal?.throwIfAborted();
  if (!settled.ok) {
    if (settled.status === 401) notifyTerminalUnauthorized(session);
    throw new ApiError(settled.status, settled.error, settled.code);
  }
  return settled;
}
