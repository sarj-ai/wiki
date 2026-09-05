import type { ActionResult } from "@/lib/action-result";
import { boundedTransformText } from "@/lib/bounded-transform-text";

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
    await response.body?.cancel();
    return {
      ok: false,
      status: response.status,
      error: "Document transform request could not be completed",
    };
  }
  return { ok: true, data: await boundedTransformText(response.body) };
}
