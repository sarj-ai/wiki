import "server-only";

import { discardStream } from "@/lib/discard-stream";

export async function fetchBackend(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const response = await fetch(input, { ...init, redirect: "manual" });
  if (response.status >= 300 && response.status < 400) {
    discardStream(response.body);
    return new Response(null, { status: 502 });
  }
  return response;
}
