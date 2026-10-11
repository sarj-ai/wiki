import { discardStream } from "@/lib/discard-stream";

export async function boundedText(
  body: null | ReadableStream<Uint8Array>,
  maxBytes: number,
  signal?: AbortSignal,
  limitMessage = "Response body exceeds the safe limit",
): Promise<string> {
  signal?.throwIfAborted();
  if (!body) return "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  const fragments: string[] = [];
  const options: StreamPipeOptions = { preventCancel: true };
  if (signal !== undefined) options.signal = signal;
  try {
    await body.pipeTo(
      new WritableStream<Uint8Array>({
        write(value) {
          size += value.byteLength;
          if (size > maxBytes) throw new RangeError(limitMessage);
          const text = decoder.decode(value, { stream: true });
          if (text) fragments.push(text);
        },
      }),
      options,
    );
    fragments.push(decoder.decode());
    return fragments.join("");
  } finally {
    discardStream(body);
  }
}
