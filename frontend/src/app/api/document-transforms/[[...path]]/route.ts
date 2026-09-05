import { cookies } from "next/headers";
import * as z from "zod";

import {
  createDocumentTransformAction,
  getDocumentTransformCapabilitiesAction,
  getSmartCreateTaskAction,
} from "@/app/actions/smart-create-actions";
import type { ActionResult } from "@/lib/action-result";
import { boundedTransformText } from "@/lib/bounded-transform-text";
import { readSmartCreateApi } from "@/lib/smart-create-server-client";

interface RouteContext {
  params: Promise<{ path?: string[] }>;
}

const UuidSchema = z.uuid();
const RESPONSE_HEADERS = {
  "cache-control": "private, no-store",
  "content-type": "application/json",
  "x-content-type-options": "nosniff",
} as const;

function failure(status: number): Response {
  return Response.json(
    { error: "Document transform request could not be completed" },
    { status, headers: RESPONSE_HEADERS },
  );
}

function trustedRequest(request: Request): boolean {
  const site = request.headers.get("sec-fetch-site");
  return (
    request.headers.get("x-wiki-transform") === "1" &&
    (site === null || site === "same-origin") &&
    (request.method === "GET" || request.headers.get("origin") === new URL(request.url).origin)
  );
}

function resultResponse(result: ActionResult<string>): Response {
  return result.ok
    ? new Response(result.data, { headers: RESPONSE_HEADERS })
    : failure(result.status);
}

async function post(request: Request, context: RouteContext): Promise<Response> {
  if (!trustedRequest(request)) return failure(403);
  const { path = [] } = await context.params;
  const key = UuidSchema.safeParse(request.headers.get("idempotency-key"));
  if (path.length !== 0 || !key.success) return failure(400);
  if (request.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/json")
    return failure(415);
  try {
    const body = await boundedTransformText(request.body);
    // Invoke the existing server-side auth and fail-closed edge limiter directly;
    // the browser references this stable route, not a deployment-specific action ID.
    return resultResponse(await createDocumentTransformAction(body, key.data));
  } catch (error: unknown) {
    return failure(error instanceof RangeError ? 413 : 502);
  }
}

async function get(request: Request, context: RouteContext): Promise<Response> {
  if (!trustedRequest(request)) return failure(403);
  const { path = [] } = await context.params;
  try {
    return await readPath(path);
  } catch {
    return failure(502);
  }
}

async function readPath(path: string[]): Promise<Response> {
  if (path.length === 1 && path[0] === "capabilities") {
    return resultResponse(await getDocumentTransformCapabilitiesAction());
  }
  const id = UuidSchema.safeParse(path[1]);
  if (path.length !== 2 || !id.success) return failure(400);
  if (path[0] === "tasks") return resultResponse(await getSmartCreateTaskAction(id.data));
  if (path[0] !== "admissions") return failure(404);
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (!token) return failure(401);
  const result = await readSmartCreateApi(`/document-transforms/admissions/${id.data}`, token);
  return result.ok
    ? new Response(result.body, { headers: RESPONSE_HEADERS })
    : failure(result.status);
}

export { get as GET, post as POST };
