import { z } from "zod";

import { ApiError, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { captureBrowserSession } from "@/lib/browser-session";
import { discardStream } from "@/lib/discard-stream";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";
import { sessionNeedsRefresh } from "@/lib/session-needs-refresh";

const ErrorSchema = z.object({ detail: z.string().optional() });

export async function wikiReadHttp<S extends z.ZodType>(
  path: string,
  schema: S,
  query: Record<string, number | string | undefined> = {},
  signal?: AbortSignal,
): Promise<z.output<S>> {
  const session = captureBrowserSession();
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const search = params.size > 0 ? `?${params.toString()}` : "";
  const requestSignal = AbortSignal.any([
    session,
    AbortSignal.timeout(30_000),
    ...(signal === undefined ? [] : [signal]),
  ]);
  requestSignal.throwIfAborted();
  if (sessionNeedsRefresh()) await refreshWikiSession(requestSignal, session);
  const read = async () =>
    await fetch(`/api/wiki${path}${search}`, {
      cache: "no-store",
      headers: { "x-wiki-read": "1", "x-request-id": crypto.randomUUID() },
      signal: requestSignal,
    });
  let response = await read();
  if (requestSignal.aborted) {
    discardStream(response.body);
    requestSignal.throwIfAborted();
  }
  if (response.status === 401) {
    discardStream(response.body);
    await refreshWikiSession(requestSignal, session);
    response = await read();
  }
  requestSignal.throwIfAborted();
  if (!response.ok) {
    if (response.status === 401) notifyTerminalUnauthorized(session);
    const payload: unknown = await response.json().catch(() => null);
    requestSignal.throwIfAborted();
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

async function refreshWikiSession(signal: AbortSignal, session: AbortSignal): Promise<void> {
  const refresh = await deduplicatedRefresh(signal, session);
  signal.throwIfAborted();
  if (refresh === "unavailable") throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
  if (refresh === "unauthorized") {
    notifyTerminalUnauthorized(session);
    throw new ApiError(401, "Your session has expired");
  }
}
