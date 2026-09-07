import type { ActionErrorCode, ActionResult } from "@/lib/action-result";
import { boundedTransformText } from "@/lib/bounded-transform-text";

const GENERIC_ERROR = "Document transform request could not be completed";

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
      const parsed: unknown = JSON.parse(await boundedTransformText(response.body));
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        "error" in parsed &&
        typeof parsed.error === "string"
      ) {
        error = parsed.error;
      }
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        "code" in parsed &&
        parsed.code === "document_transform_source_changed"
      ) {
        code = parsed.code;
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
