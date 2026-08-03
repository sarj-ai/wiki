"use client";

import type { ActionResult } from "@/lib/action-result";
import { ApiError } from "@/lib/api-error";
import { deduplicatedRefresh } from "@/lib/session-client";

export async function runAction<T>(action: () => Promise<ActionResult<T>>): Promise<T> {
  let settled = await action();
  if (!settled.ok && settled.status === 401 && (await deduplicatedRefresh())) {
    settled = await action();
  }
  if (!settled.ok) throw new ApiError(settled.status, settled.error);
  return settled.data;
}
