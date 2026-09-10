import { z } from "zod";

import { frontendReleaseSha } from "@/lib/env";

const MAX_BODY_BYTES = 2048;
const PhaseSchema = z.enum(["click", "commit", "toolbarPaint", "iframeMount", "iframeLoad"]);
const VisibilitySchema = z.enum(["hidden", "visible"]);
const ZPERF_EVENT = z.strictObject({
  durationMs: z.number().min(0).max(120_000),
  navigationId: z.uuid(),
  phase: PhaseSchema,
  saveData: z.boolean().optional(),
  visibility: VisibilitySchema.optional(),
});

async function post(request: Request): Promise<Response> {
  if (!hasSessionCookie(request.headers.get("cookie"))) {
    return new Response(null, { status: 401 });
  }
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return new Response(null, { status: 413 });
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) {
    return new Response(null, { status: 413 });
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(body);
  } catch {
    return new Response(null, { status: 400 });
  }
  const parsed = ZPERF_EVENT.safeParse(decoded);
  if (!parsed.success) return new Response(null, { status: 400 });

  const ray = request.headers.get("cf-ray")?.split("-").at(-1);
  console.warn(
    JSON.stringify({
      event: "wiki.performance",
      ...parsed.data,
      ...(ray === undefined ? {} : { colo: ray }),
      ...(frontendReleaseSha() === undefined ? {} : { release: frontendReleaseSha() }),
    }),
  );
  return new Response(null, {
    headers: { "Cache-Control": "private, no-store" },
    status: 204,
  });
}

function hasSessionCookie(header: null | string): boolean {
  if (header === null) return false;
  return /(?:^|;\s*)(?:access_token|refresh_token)=/.test(header);
}

export { post as POST };
