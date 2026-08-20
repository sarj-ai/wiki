"use client";

import type { ActionResult } from "@/lib/action-result";
import { ApiError } from "@/lib/api-error";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";

export async function runAction<T>(action: () => Promise<ActionResult<T>>): Promise<T> {
  let settled = await action();
  if (!settled.ok && settled.status === 401 && (await deduplicatedRefresh())) {
    settled = await action();
  }
  if (!settled.ok) {
    if (settled.status === 401) notifyTerminalUnauthorized();
    throw new ApiError(settled.status, settled.error);
  }
  return settled.data;
}
