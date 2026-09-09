"use client";

import type { ActionResult } from "@/lib/action-result";
import { ApiError, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";

export async function runAction<T>(action: () => Promise<ActionResult<T>>): Promise<T> {
  const result = await runActionResult(action);
  return result.data;
}

export async function runActionResult<T>(
  action: () => Promise<ActionResult<T>>,
): Promise<Extract<ActionResult<T>, { ok: true }>> {
  let settled = await action();
  if (!settled.ok && settled.status === 401) {
    const refresh = await deduplicatedRefresh();
    if (refresh === "refreshed") settled = await action();
    if (refresh === "unavailable") {
      throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    }
  }
  if (!settled.ok) {
    if (settled.status === 401) notifyTerminalUnauthorized();
    throw new ApiError(settled.status, settled.error, settled.code);
  }
  return settled;
}
