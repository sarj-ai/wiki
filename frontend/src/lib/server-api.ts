import "server-only";
import { cookies } from "next/headers";

import { camelizeParsed } from "@/lib/camelize";

// Inlined at build time; the Worker has no private route to the API host.
const API_URL = process.env["NEXT_PUBLIC_API_URL"] ?? "http://localhost:8000";

export class ServerApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Server API error: ${status}`);
    this.status = status;
    this.name = "ServerApiError";
  }
}

/**
 * Server-side GET forwarding the access_token cookie. Not the ky
 * client — no cookie jar server-side, and the 401-refresh hook can't
 * set cookies from RSC. A missing token fails fast; prefetchQuery
 * swallows it and the client recovers via ky's silent refresh.
 */
export async function serverGet<T>(path: string): Promise<T> {
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) {
    throw new ServerApiError(401);
  }
  const response = await fetch(`${API_URL}/${path}`, {
    headers: { cookie: `access_token=${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ServerApiError(response.status);
  }
  const parsed: unknown = await response.json();
  return camelizeParsed(parsed) as T;
}
