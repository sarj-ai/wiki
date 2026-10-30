import * as z from "zod";

import type { ActionErrorCode, ActionResult } from "@/lib/action-result";
import { boundedErrorText } from "@/lib/bounded-error-text";
import { boundedTransformText } from "@/lib/bounded-transform-text";
import { captureBrowserSession } from "@/lib/browser-session";
import { discardStream } from "@/lib/discard-stream";

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
  const session = captureBrowserSession();
  // Reads retire with their session; a POST that reached admission can finish.
  const requestSignal = AbortSignal.any([
    AbortSignal.timeout(35_000),
    ...(body === undefined ? [session] : []),
    ...(signal === undefined ? [] : [signal]),
  ]);
  requestSignal.throwIfAborted();
  const response = await fetch(`/api/document-transforms${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      "x-wiki-transform": "1",
      ...(!(body === undefined) && { "content-type": "application/json" }),
      ...(!(key === undefined) && { "idempotency-key": key }),
    },
    ...(!(body === undefined) && { body }),
    signal: requestSignal,
  });
  // Once headers arrive, a retired owner need not read the mutation's response.
  const responseSignal =
    body === undefined ? requestSignal : AbortSignal.any([requestSignal, session]);
  if (responseSignal.aborted) {
    discardStream(response.body);
    responseSignal.throwIfAborted();
  }
  if (!response.ok) {
    let error = GENERIC_ERROR;
    let code: ActionErrorCode | undefined;
    try {
      const parsed = TransformErrorSchema.safeParse(
        JSON.parse(await boundedErrorText(response.body, responseSignal)),
      );
      if (parsed.success) {
        error = parsed.data.error ?? GENERIC_ERROR;
        code = parsed.data.code;
      }
    } catch {
      // The same-origin route owns error text; malformed responses stay redacted.
    }
    responseSignal.throwIfAborted();
    return {
      ok: false,
      status: response.status,
      error,
      ...(!(code === undefined) && { code }),
    };
  }
  const replayHeader = response.headers.get("idempotency-replayed");
  const responseBody = await boundedTransformText(response.body, responseSignal);
  responseSignal.throwIfAborted();
  return {
    ok: true,
    data: responseBody,
    ...(body !== undefined &&
      (replayHeader === "true" || replayHeader === "false") && {
        replayed: replayHeader === "true",
      }),
  };
}
