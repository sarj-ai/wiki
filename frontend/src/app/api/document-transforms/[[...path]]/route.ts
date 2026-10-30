import * as z from "zod";

import {
  createDocumentTransformAction,
  createDocumentTransformUploadAction,
} from "@/app/actions/smart-create-actions";
import type { ActionErrorCode, ActionResult } from "@/lib/action-result";
import { boundedTransformText } from "@/lib/bounded-transform-text";
import { wikiReadResponse } from "@/lib/clients/wiki-read-response";

interface RouteContext {
  params: Promise<{ path?: string[] }>;
}

const UuidSchema = z.uuid();
const TransformModeSchema = z.enum(["both", "format", "unslop"]);
const RESPONSE_HEADERS = {
  "cache-control": "private, no-store",
  "content-type": "application/json",
  "x-content-type-options": "nosniff",
} as const;
const SOURCE_CHANGED_MESSAGE =
  "The document changed since polishing was requested. Reload it before trying again.";
const UPSTREAM_TIMEOUT_MS = 30_000;

function failure(status: number, code?: ActionErrorCode): Response {
  const sourceChanged = status === 409 && code === "document_transform_source_changed";
  return Response.json(
    {
      ...(sourceChanged && { code }),
      error: sourceChanged
        ? SOURCE_CHANGED_MESSAGE
        : "Document transform request could not be completed",
    },
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

async function post(request: Request, context: RouteContext): Promise<Response> {
  if (!trustedRequest(request)) return failure(403);
  const { path = [] } = await context.params;
  if (request.signal.aborted) return failure(502);
  if (path.length === 1 && path[0] === "uploads") {
    if (request.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/json")
      return failure(415);
    return resultResponse(await createDocumentTransformUploadAction());
  }
  const key = UuidSchema.safeParse(request.headers.get("idempotency-key"));
  if (path.length !== 0 || !key.success) return failure(400);
  if (request.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/json")
    return failure(415);
  try {
    const ingressSignal = AbortSignal.any([
      request.signal,
      AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    ]);
    const body = await boundedTransformText(request.body, ingressSignal);
    ingressSignal.throwIfAborted();
    // Invoke the existing server-side auth and fail-closed edge limiter directly;
    // the browser references this stable route, not a deployment-specific action ID.
    return resultResponse(await createDocumentTransformAction(body, key.data), true);
  } catch (error: unknown) {
    return failure(error instanceof RangeError ? 413 : 502);
  }
}

async function get(request: Request, context: RouteContext): Promise<Response> {
  if (!trustedRequest(request)) return failure(403);
  const { path = [] } = await context.params;
  try {
    return await readPath(path, request);
  } catch {
    return failure(502);
  }
}

function resultResponse(result: ActionResult<string>, exposeReplay = false): Response {
  if (!result.ok) return failure(result.status, result.code);
  const headers = new Headers(RESPONSE_HEADERS);
  if (exposeReplay && result.replayed !== undefined) {
    headers.set("Idempotency-Replayed", String(result.replayed));
  }
  return new Response(result.data, { headers });
}

async function readPath(path: string[], request: Request): Promise<Response> {
  if (path.length === 1 && path[0] === "capabilities") {
    return await wikiReadResponse(
      request,
      "/document-transforms/capabilities",
      failure,
      UPSTREAM_TIMEOUT_MS,
    );
  }
  if (path.length === 1 && path[0] === "current") {
    const url = new URL(request.url);
    const fileId = url.searchParams.get("file_id");
    const mode = TransformModeSchema.safeParse(url.searchParams.get("mode"));
    if (!fileId || fileId.length > 128 || !mode.success) return failure(400);
    return await wikiReadResponse(
      request,
      `/document-transforms/current?file_id=${encodeURIComponent(fileId)}&mode=${encodeURIComponent(mode.data)}`,
      failure,
      UPSTREAM_TIMEOUT_MS,
    );
  }
  const id = UuidSchema.safeParse(path[1]);
  if (path.length !== 2 || !id.success) return failure(400);
  if (path[0] === "tasks")
    return await wikiReadResponse(request, `/tasks/${id.data}`, failure, UPSTREAM_TIMEOUT_MS);
  if (path[0] !== "admissions") return failure(404);
  return await wikiReadResponse(
    request,
    `/document-transforms/admissions/${id.data}`,
    failure,
    UPSTREAM_TIMEOUT_MS,
  );
}

export { get as GET, post as POST };
