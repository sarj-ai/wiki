export async function boundedText(
  body: null | ReadableStream<Uint8Array>,
  maxBytes: number,
  signal?: AbortSignal,
  limitMessage = "Response body exceeds the safe limit",
): Promise<string> {
  signal?.throwIfAborted();
  if (!body) return "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const reader = body.getReader();
  let size = 0;
  const fragments: string[] = [];
  let discarded = false;
  const discard = () => {
    if (discarded) return;
    discarded = true;
    // Cancelling closes pending reads immediately even when source cleanup stalls.
    void reader.cancel().catch(() => null);
  };
  const chunks: AsyncIterable<Uint8Array> = {
    [Symbol.asyncIterator]: () => ({ next: reader.read.bind(reader) }),
  };
  signal?.addEventListener("abort", discard, { once: true });
  try {
    signal?.throwIfAborted();
    for await (const value of chunks) {
      signal?.throwIfAborted();
      size += value.byteLength;
      if (size > maxBytes) throw new RangeError(limitMessage);
      const text = decoder.decode(value, { stream: true });
      if (text) fragments.push(text);
    }
    signal?.throwIfAborted();
    fragments.push(decoder.decode());
    return fragments.join("");
  } finally {
    signal?.removeEventListener("abort", discard);
    discard();
    reader.releaseLock();
  }
}
