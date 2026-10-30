import "server-only";

import * as z from "zod";

import type { ActionErrorCode } from "@/lib/action-result";
import { boundedErrorText } from "@/lib/bounded-error-text";
import { boundedTransformText } from "@/lib/bounded-transform-text";
import { fetchBackend } from "@/lib/clients/fetch-backend";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";

const ErrorResponseSchema = z.object({ detail: z.string().optional() });
const UPSTREAM_TIMEOUT_MS = 30_000;

export interface SmartUpstreamResponse {
  body: string;
  error?: string;
  errorCode?: ActionErrorCode;
  idempotencyReplayed?: boolean;
  ok: boolean;
  status: number;
}

export async function smartCreateServerClient(
  path: string,
  body: string,
  token: string,
  idempotencyKey?: string,
): Promise<SmartUpstreamResponse> {
  const headers = new Headers({
    Authorization: `Bearer ${token}`,
    "content-type": "application/json",
  });
  if (idempotencyKey !== undefined) headers.set("idempotency-key", idempotencyKey);

  try {
    const signal = AbortSignal.timeout(UPSTREAM_TIMEOUT_MS);
    const response = await fetchBackend(`${API_URL}${path}`, {
      body,
      cache: "no-store",
      headers,
      method: "POST",
      signal,
    });
    if (signal.aborted) {
      discardStream(response.body);
      signal.throwIfAborted();
    }
    const responseBody = await (response.ok ? boundedTransformText : boundedErrorText)(
      response.body,
      signal,
    );
    signal.throwIfAborted();
    return smartCreateResponse(response, responseBody);
  } catch {
    return { body: "", error: "Smart Create could not reach the API", ok: false, status: 502 };
  }
}

function smartCreateResponse(response: Response, body: string): SmartUpstreamResponse {
  if (response.ok) {
    const replayHeader = response.headers.get("idempotency-replayed");
    return {
      body,
      ...((replayHeader === "true" || replayHeader === "false") && {
        idempotencyReplayed: replayHeader === "true",
      }),
      ok: true,
      status: response.status,
    };
  }
  const parsed = ErrorResponseSchema.safeParse(safeJson(body));
  const error = parsed.success ? parsed.data.detail : undefined;
  const rawErrorCode = response.headers.get("x-wiki-error-code");
  const errorCode =
    response.status === 409 && rawErrorCode === "document_transform_source_changed"
      ? rawErrorCode
      : undefined;
  return {
    body,
    ...(!(error === undefined) && { error }),
    ...(!(errorCode === undefined) && { errorCode }),
    ok: false,
    status: response.status,
  };
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}
