import { RATE_LIMIT_MESSAGE } from "@/lib/api-error";
import { wikiReadResponse } from "@/lib/clients/wiki-read-response";

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

const READ_PATHS = [
  /^auth\/me$/u,
  /^sidebar\/drives$/u,
  /^sidebar\/drives\/[A-Za-z0-9_-]{1,128}\/(?:folders|permissions)$/u,
  /^sidebar\/folders\/[A-Za-z0-9_-]{1,128}\/files$/u,
  /^files\/[A-Za-z0-9_-]{1,128}$/u,
  /^aliases\/files\/[A-Za-z0-9_-]{1,128}$/u,
  /^extras\/(?:preferences|folder-icons|doc-icons|sort-order)$/u,
  /^drive-search(?:\/snippet)?$/u,
  /^tasks\/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/iu,
  /^templates$/u,
] as const;
// IDs use the ASCII classes above. The longest path is sidebar/drives/<128>/permissions.
const MAX_READ_PATH_LENGTH = 155;
const RESPONSE_HEADERS = {
  "cache-control": "private, no-store",
  "content-type": "application/json",
  "x-content-type-options": "nosniff",
} as const;
const EXPIRED_BODY = JSON.stringify({ detail: "Your session has expired" });
const RATE_LIMIT_BODY = JSON.stringify({ detail: RATE_LIMIT_MESSAGE });
const FAILURE_BODY = JSON.stringify({ detail: "The request could not be completed" });

async function get(request: Request, context: RouteContext): Promise<Response> {
  const site = request.headers.get("sec-fetch-site");
  if (request.headers.get("x-wiki-read") !== "1" || (site !== null && site !== "same-origin")) {
    return failure(403);
  }
  const { path } = await context.params;
  if (!withinReadPathLimit(path)) return failure(404);
  const pathname = path.join("/");
  if (!READ_PATHS.some((pattern) => pattern.test(pathname))) return failure(404);
  return await wikiReadResponse(request, `/${pathname}${new URL(request.url).search}`, failure);
}

function withinReadPathLimit(path: string[]): boolean {
  let length = Math.max(0, path.length - 1);
  if (length > MAX_READ_PATH_LENGTH) return false;
  for (const segment of path) {
    length += segment.length;
    if (length > MAX_READ_PATH_LENGTH) return false;
  }
  return true;
}

function failure(status: number): Response {
  return new Response(
    status === 401 ? EXPIRED_BODY : status === 429 ? RATE_LIMIT_BODY : FAILURE_BODY,
    { status, headers: RESPONSE_HEADERS },
  );
}

export { get as GET };
