import * as z from "zod";

import type { ActionErrorCode, ActionResult } from "@/lib/action-result";
import { boundedTransformText } from "@/lib/bounded-transform-text";

const GENERIC_ERROR = "Document transform request could not be completed";
const TransformErrorSchema = z.object({
  code: z.literal("document_transform_source_changed").optional().catch(undefined),
  error: z.string().optional().catch(undefined),
});

export async function transformHttp(
  path: string,
  body?: string,
  key?: string,
  signal?: AbortSignal,
): Promise<ActionResult<string>> {
  const requestSignal = signal
    ? AbortSignal.any([signal, AbortSignal.timeout(35_000)])
    : AbortSignal.timeout(35_000);
  requestSignal.throwIfAborted();
  const response = await fetch(`/api/document-transforms${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      "x-wiki-transform": "1",
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...(key === undefined ? {} : { "idempotency-key": key }),
    },
    ...(body === undefined ? {} : { body }),
    signal: requestSignal,
  });
  if (!response.ok) {
    let error = GENERIC_ERROR;
    let code: ActionErrorCode | undefined;
    try {
      const parsed = TransformErrorSchema.safeParse(
        JSON.parse(await boundedTransformText(response.body, requestSignal)),
      );
      if (parsed.success) {
        error = parsed.data.error ?? GENERIC_ERROR;
        code = parsed.data.code;
      }
    } catch {
      // The same-origin route owns error text; malformed responses stay redacted.
    }
    requestSignal.throwIfAborted();
    return {
      ok: false,
      status: response.status,
      error,
      ...(code === undefined ? {} : { code }),
    };
  }
  const replayHeader = response.headers.get("idempotency-replayed");
  const responseBody = await boundedTransformText(response.body, requestSignal);
  requestSignal.throwIfAborted();
  return {
    ok: true,
    data: responseBody,
    ...(body === undefined
      ? {}
      : replayHeader === "true" || replayHeader === "false"
        ? { replayed: replayHeader === "true" }
        : {}),
  };
}
