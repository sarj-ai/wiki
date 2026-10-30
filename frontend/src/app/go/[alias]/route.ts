import { cookies } from "next/headers";

import { resolveDocumentAlias } from "@/lib/alias-client";
import { zDocumentAliasWrite } from "@/lib/generated/zod.gen";

const PRIVATE_NO_STORE = { "cache-control": "private, no-store" };

async function get(request: Request, context: RouteContext): Promise<Response> {
  const { alias } = await context.params;
  if (
    !zDocumentAliasWrite.safeParse({ alias }).success ||
    new URL(request.url).pathname !== `/go/${alias}`
  ) {
    return notFound();
  }

  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) return response("Unauthorized", 401);

  const result = await resolveDocumentAlias(alias, token);
  if (result.status === "unauthorized") return response("Unauthorized", 401);
  if (result.status === "not_found") return notFound();
  if (result.status === "unavailable") return response("Unable to resolve short link", 502);
  const destination = new URL(`/d/${encodeURIComponent(result.fileId)}`, request.url);
  destination.searchParams.set("go", alias);
  return new Response(null, {
    headers: {
      ...PRIVATE_NO_STORE,
      location: `${destination.pathname}${destination.search}`,
    },
    status: 302,
  });
}

function notFound(): Response {
  return response("Short link not found", 404);
}

function response(body: string, status: number): Response {
  return new Response(body, { headers: PRIVATE_NO_STORE, status });
}

interface RouteContext {
  params: Promise<{ alias: string }>;
}

export { get as GET };
