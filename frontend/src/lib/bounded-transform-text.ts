const MAX_TRANSFORM_BODY_BYTES = 4 * 1024 * 1024;

export async function boundedTransformText(
  body: ReadableStream<Uint8Array> | null,
): Promise<string> {
  if (!body) return "";
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return text + decoder.decode();
      size += value.byteLength;
      if (size > MAX_TRANSFORM_BODY_BYTES)
        throw new RangeError("Transform body exceeds the safe limit");
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    await reader.cancel().catch(() => null);
    reader.releaseLock();
  }
}
