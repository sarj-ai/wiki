import { frontendReleaseSha } from "@/lib/env";

const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,64}$/;

function get(request: Request): Response {
  const requestId = requestIdFor(request);
  const revision = frontendReleaseSha();
  if (revision === undefined) {
    return Response.json({ status: "error" }, { headers: responseHeaders(requestId), status: 503 });
  }
  return Response.json({ revision, status: "ok" }, { headers: responseHeaders(requestId) });
}

function requestIdFor(request: Request): string {
  const requested = request.headers.get("X-Request-ID");
  return requested !== null && REQUEST_ID_PATTERN.test(requested) ? requested : crypto.randomUUID();
}

function responseHeaders(requestId: string): HeadersInit {
  return {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Request-ID": requestId,
  };
}

export { get as GET };
