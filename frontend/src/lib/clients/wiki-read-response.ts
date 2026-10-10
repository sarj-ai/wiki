import "server-only";

import { cookies } from "next/headers";

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
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (!token) return failure(401);
  const inboundId = request.headers.get("x-request-id");
  const requestId = inboundId && REQUEST_ID.test(inboundId) ? inboundId : crypto.randomUUID();
  try {
    const upstream = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      headers: { authorization: `Bearer ${token}`, "x-request-id": requestId },
      redirect: "error",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(timeoutMs)]),
    });
    if (!upstream.ok) {
      await upstream.body?.cancel();
      return failure(upstream.status);
    }
    const headers = new Headers(RESPONSE_HEADERS);
    headers.set("x-request-id", requestId);
    const timing = upstream.headers.get("server-timing");
    if (timing) headers.set("server-timing", timing);
    return new Response(upstream.body, { headers });
  } catch {
    return failure(502);
  }
}
