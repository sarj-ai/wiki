import { z } from "zod";

import { ApiError, RATE_LIMIT_MESSAGE, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { boundedErrorText } from "@/lib/bounded-error-text";
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
  let params: undefined | URLSearchParams;
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) (params ??= new URLSearchParams()).set(key, String(value));
  }
  const search = params === undefined ? "" : `?${params.toString()}`;
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
  if (requestSignal.aborted) {
    discardStream(response.body);
    requestSignal.throwIfAborted();
  }
  if (!response.ok) {
    if (response.status === 401) notifyTerminalUnauthorized(session);
    if (response.status === 429) {
      discardStream(response.body);
      throw new ApiError(429, RATE_LIMIT_MESSAGE);
    }
    let payload: unknown = null;
    try {
      payload = JSON.parse(await boundedErrorText(response.body, requestSignal));
    } catch {
      // Error bodies are optional; malformed, oversized and stalled bodies stay redacted.
    }
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
