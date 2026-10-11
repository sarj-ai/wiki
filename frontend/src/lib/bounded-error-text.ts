import { boundedText } from "@/lib/bounded-text";

const MAX_ERROR_BODY_BYTES = 32 * 1024;
const ERROR_BODY_TIMEOUT_MS = 10_000;

// Error guidance needs neither a full success payload nor an open-ended stream.
export async function boundedErrorText(
  body: null | ReadableStream<Uint8Array>,
  signal: AbortSignal,
): Promise<string> {
  return await boundedText(
    body,
    MAX_ERROR_BODY_BYTES,
    AbortSignal.any([signal, AbortSignal.timeout(ERROR_BODY_TIMEOUT_MS)]),
  );
}
