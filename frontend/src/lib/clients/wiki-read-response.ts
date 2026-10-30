import "server-only";

import { cookies } from "next/headers";

import { fetchBackend } from "@/lib/clients/fetch-backend";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";

const REQUEST_ID = /^[A-Za-z0-9._:-]{1,64}$/u;
const RESPONSE_HEADERS = {
  "cache-control": "private, no-store",
  "content-type": "application/json",
  "x-content-type-options": "nosniff",
} as const;

// Routes validate their own read allowlist and own the redacted error policy.
export async function wikiReadResponse(
  request: Request,
  path: string,
  failure: (status: number) => Response,
  timeoutMs = 25_000,
): Promise<Response> {
  const inboundId = request.headers.get("x-request-id");
  const requestId = inboundId && REQUEST_ID.test(inboundId) ? inboundId : crypto.randomUUID();
  const fail = (status: number): Response => {
    const response = failure(status);
    response.headers.set("x-request-id", requestId);
    return response;
  };
  if (request.signal.aborted) return fail(502);
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (!token) return fail(401);
  try {
    request.signal.throwIfAborted();
    const upstreamSignal = AbortSignal.any([request.signal, AbortSignal.timeout(timeoutMs)]);
    const upstream = await fetchBackend(`${API_URL}${path}`, {
      cache: "no-store",
      headers: { authorization: `Bearer ${token}`, "x-request-id": requestId },
      signal: upstreamSignal,
    });
    if (upstreamSignal.aborted) {
      discardStream(upstream.body);
      return fail(502);
    }
    if (!upstream.ok) {
      discardStream(upstream.body);
      return fail(upstream.status);
    }
    const headers = new Headers(RESPONSE_HEADERS);
    headers.set("x-request-id", requestId);
    const timing = upstream.headers.get("server-timing");
    if (timing) headers.set("server-timing", timing);
    return new Response(upstream.body, { headers });
  } catch {
    return fail(502);
  }
}
