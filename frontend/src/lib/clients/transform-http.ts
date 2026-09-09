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
): Promise<ActionResult<string>> {
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
    signal: AbortSignal.timeout(35_000),
  });
  if (!response.ok) {
    let error = GENERIC_ERROR;
    let code: ActionErrorCode | undefined;
    try {
      const parsed = TransformErrorSchema.safeParse(
        JSON.parse(await boundedTransformText(response.body)),
      );
      if (parsed.success) {
        error = parsed.data.error ?? GENERIC_ERROR;
        code = parsed.data.code;
      }
    } catch {
      // The same-origin route owns error text; malformed responses stay redacted.
    }
    return {
      ok: false,
      status: response.status,
      error,
      ...(code === undefined ? {} : { code }),
    };
  }
  const replayHeader = response.headers.get("idempotency-replayed");
  return {
    ok: true,
    data: await boundedTransformText(response.body),
    ...(body === undefined
      ? {}
      : replayHeader === "true" || replayHeader === "false"
        ? { replayed: replayHeader === "true" }
        : {}),
  };
}
