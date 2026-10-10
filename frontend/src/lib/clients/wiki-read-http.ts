import { z } from "zod";

import { ApiError, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";

const ErrorSchema = z.object({ detail: z.string().optional() });

export async function wikiReadHttp<S extends z.ZodType>(
  path: string,
  schema: S,
  query: Record<string, number | string | undefined> = {},
  signal?: AbortSignal,
): Promise<z.output<S>> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const search = params.size > 0 ? `?${params.toString()}` : "";
  const requestSignal = signal
    ? AbortSignal.any([signal, AbortSignal.timeout(30_000)])
    : AbortSignal.timeout(30_000);
  requestSignal.throwIfAborted();
  const read = async () =>
    await fetch(`/api/wiki${path}${search}`, {
      cache: "no-store",
      headers: { "x-wiki-read": "1", "x-request-id": crypto.randomUUID() },
      signal: requestSignal,
    });
  let response = await read();
  if (response.status === 401) {
    await response.body?.cancel();
    const refresh = await deduplicatedRefresh(requestSignal);
    requestSignal.throwIfAborted();
    if (refresh === "unavailable") throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    if (refresh === "unauthorized") {
      notifyTerminalUnauthorized();
      throw new ApiError(401, "Your session has expired");
    }
    response = await read();
  }
  requestSignal.throwIfAborted();
  if (!response.ok) {
    if (response.status === 401) notifyTerminalUnauthorized();
    const payload: unknown = await response.json().catch(() => null);
    const error = ErrorSchema.safeParse(payload);
    throw new ApiError(
      response.status,
      error.success
        ? (error.data.detail ?? "The request could not be completed")
        : "The request could not be completed",
    );
  }
  const payload: unknown = await response.json();
  requestSignal.throwIfAborted();
  return schema.parse(payload);
}
