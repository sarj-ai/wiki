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
const RESPONSE_HEADERS = {
  "cache-control": "private, no-store",
  "content-type": "application/json",
  "x-content-type-options": "nosniff",
} as const;

async function get(request: Request, context: RouteContext): Promise<Response> {
  const site = request.headers.get("sec-fetch-site");
  if (request.headers.get("x-wiki-read") !== "1" || (site !== null && site !== "same-origin")) {
    return failure(403);
  }
  const { path } = await context.params;
  const pathname = path.join("/");
  if (!READ_PATHS.some((pattern) => pattern.test(pathname))) return failure(404);
  return await wikiReadResponse(request, `/${pathname}${new URL(request.url).search}`, failure);
}

function failure(status: number): Response {
  return Response.json(
    {
      detail:
        status === 401
          ? "Your session has expired"
          : status === 429
            ? RATE_LIMIT_MESSAGE
            : "The request could not be completed",
    },
    { status, headers: RESPONSE_HEADERS },
  );
}

export { get as GET };
