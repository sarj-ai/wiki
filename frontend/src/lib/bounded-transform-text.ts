import { boundedText } from "@/lib/bounded-text";

const MAX_TRANSFORM_BODY_BYTES = 4 * 1024 * 1024;

export async function boundedTransformText(
  body: null | ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): Promise<string> {
  return await boundedText(
    body,
    MAX_TRANSFORM_BODY_BYTES,
    signal,
    "Transform body exceeds the safe limit",
  );
}
