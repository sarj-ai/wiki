import { cookies } from "next/headers";

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

async function get(_request: Request, context: RouteContext): Promise<Response> {
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

  const upstream = await fetch(`${API_URL}/files/${encodeURIComponent(fileId)}/content`, {
    cache: "no-store",
    headers: { cookie: `access_token=${token}` },
    signal: AbortSignal.timeout(15_000),
  });
  if (!upstream.ok) {
    upstream.body?.cancel().catch(() => null);
    return new Response("Unable to load HTML file", {
      headers: { "cache-control": "private, no-store" },
      status: upstream.status,
    });
  }

  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("cache-control", "private, no-store");
  return new Response(upstream.body, { headers, status: upstream.status });
}

interface RouteContext {
  params: Promise<{ fileId: string }>;
}

export { get as GET };
