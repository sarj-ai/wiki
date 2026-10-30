import { z } from "zod";

import { boundedText } from "@/lib/bounded-text";
import { frontendReleaseSha } from "@/lib/env";

const MAX_BODY_BYTES = 2048;
const BODY_TIMEOUT_MS = 5_000;
const PhaseSchema = z.enum(["click", "commit", "toolbarPaint", "iframeMount", "iframeLoad"]);
const VisibilitySchema = z.enum(["hidden", "visible"]);
const ZperfEventSchema = z.strictObject({
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
  let decoded: unknown;
  try {
    const ingressSignal = AbortSignal.any([request.signal, AbortSignal.timeout(BODY_TIMEOUT_MS)]);
    const body = await boundedText(request.body, MAX_BODY_BYTES, ingressSignal);
    ingressSignal.throwIfAborted();
    decoded = JSON.parse(body);
  } catch (error: unknown) {
    const status =
      error instanceof RangeError
        ? 413
        : error instanceof DOMException && error.name === "TimeoutError"
          ? 408
          : 400;
    return new Response(null, { status });
  }
  const parsed = ZperfEventSchema.safeParse(decoded);
  if (!parsed.success) return new Response(null, { status: 400 });

  const ray = request.headers.get("cf-ray")?.split("-").at(-1);
  console.warn(
    JSON.stringify({
      event: "wiki.performance",
      ...parsed.data,
      ...(!(ray === undefined) && { colo: ray }),
      ...(!(frontendReleaseSha() === undefined) && { release: frontendReleaseSha() }),
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
