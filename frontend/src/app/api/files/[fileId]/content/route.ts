import { cookies } from "next/headers";

import { fetchBackend } from "@/lib/clients/fetch-backend";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";

const DRIVE_FILE_ID = /^[A-Za-z0-9_-]{10,}$/;
const FORWARDED_HEADERS = [
  "cache-control",
  "content-security-policy",
  "content-type",
  "cross-origin-resource-policy",
  "permissions-policy",
  "referrer-policy",
  "x-content-type-options",
] as const;

async function get(request: Request, context: RouteContext): Promise<Response> {
  if (request.signal.aborted) return unavailable(502);
  const { fileId } = await context.params;
  if (!DRIVE_FILE_ID.test(fileId)) {
    return new Response("Invalid Drive file ID", {
      headers: { "cache-control": "private, no-store" },
      status: 400,
    });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) {
    return new Response("Unauthorized", {
      headers: { "cache-control": "private, no-store" },
      status: 401,
    });
  }

  try {
    const upstream = await fetchBackend(`${API_URL}/files/${encodeURIComponent(fileId)}/content`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(15_000)]),
    });
    if (!upstream.ok) {
      discardStream(upstream.body);
      return unavailable(upstream.status);
    }

    const headers = new Headers();
    for (const name of FORWARDED_HEADERS) {
      const value = upstream.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    headers.set("cache-control", "private, no-store");
    return new Response(upstream.body, { headers, status: upstream.status });
  } catch {
    return unavailable(502);
  }
}

function unavailable(status: number): Response {
  return new Response("Unable to load HTML file", {
    headers: { "cache-control": "private, no-store" },
    status,
  });
}

interface RouteContext {
  params: Promise<{ fileId: string }>;
}

export { get as GET };
