const MAX_TRANSFORM_BODY_BYTES = 4 * 1024 * 1024;

export async function boundedTransformText(
  body: null | ReadableStream<Uint8Array>,
): Promise<string> {
  if (!body) return "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  const fragments: string[] = [];
  try {
    await body.pipeTo(
      new WritableStream<Uint8Array>({
        write(value) {
          size += value.byteLength;
          if (size > MAX_TRANSFORM_BODY_BYTES)
            throw new RangeError("Transform body exceeds the safe limit");
          const text = decoder.decode(value, { stream: true });
          if (text) fragments.push(text);
        },
      }),
      { preventCancel: true },
    );
    fragments.push(decoder.decode());
    return fragments.join("");
  } finally {
    // Keep cleanup failures from replacing the original decoding or size error.
    await body.cancel().catch(() => null);
  }
}
