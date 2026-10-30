// @vitest-environment node

import { boundedTransformText } from "@/lib/bounded-transform-text";

const BODY_LIMIT = 4 * 1024 * 1024;

function streamOf(chunks: readonly Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

it("returns empty text for a missing body", async () => {
  await expect(boundedTransformText(null)).resolves.toBe("");
});

it("reports a decoding failure without waiting for a stalled cleanup", async () => {
  const cleanup = Promise.withResolvers<undefined>();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array([0xff]));
    },
    cancel: async () => await cleanup.promise,
  });
  try {
    await expect(boundedTransformText(body)).rejects.toBeInstanceOf(TypeError);
  } finally {
    cleanup.resolve(undefined);
  }
});

it("releases an empty stream", async () => {
  const body = streamOf([]);
  await expect(boundedTransformText(body)).resolves.toBe("");
  expect(body.locked).toBe(false);
});

it("decodes multibyte UTF-8 split across individual byte chunks", async () => {
  const text = "Arabic: مرحبا — 😀";
  const bytes = new TextEncoder().encode(text);
  const body = streamOf(Array.from(bytes, (byte) => new Uint8Array([byte])));
  await expect(boundedTransformText(body)).resolves.toBe(text);
  expect(body.locked).toBe(false);
});

it("ignores empty chunks between text fragments", async () => {
  const body = streamOf([new Uint8Array(), new Uint8Array([97]), new Uint8Array()]);
  await expect(boundedTransformText(body)).resolves.toBe("a");
  expect(body.locked).toBe(false);
});

it("accepts exactly the byte limit", async () => {
  const body = streamOf([new Uint8Array(BODY_LIMIT).fill(97)]);
  await expect(boundedTransformText(body)).resolves.toBe("a".repeat(BODY_LIMIT));
  expect(body.locked).toBe(false);
});

it("rejects a cumulative body one byte over the limit", async () => {
  const body = streamOf([new Uint8Array(BODY_LIMIT).fill(97), new Uint8Array([98])]);
  await expect(boundedTransformText(body)).rejects.toThrow("Transform body exceeds the safe limit");
  expect(body.locked).toBe(false);
});

it.each([
  { name: "malformed UTF-8", bytes: [0xc3, 0x28] },
  { name: "truncated UTF-8", bytes: [0xf0, 0x9f] },
])("rejects $name and releases the reader", async ({ bytes }) => {
  const body = streamOf([new Uint8Array(bytes)]);
  await expect(boundedTransformText(body)).rejects.toBeInstanceOf(TypeError);
  expect(body.locked).toBe(false);
});

it("preserves an upstream read failure and releases the reader", async () => {
  const failure = new Error("upstream disconnected");
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(failure);
    },
  });
  await expect(boundedTransformText(body)).rejects.toBe(failure);
  expect(body.locked).toBe(false);
});

it.each([
  { name: "malformed", bytes: new Uint8Array([0xff]), error: TypeError },
  { name: "oversized", bytes: new Uint8Array(BODY_LIMIT + 1), error: RangeError },
])("cancels an open $name stream without masking its error", async ({ bytes, error }) => {
  const cancel = vi.fn<() => Promise<void>>().mockRejectedValue(new Error("cleanup failed"));
  const body = new ReadableStream<Uint8Array>({
    cancel,
    start(controller) {
      controller.enqueue(bytes);
    },
  });
  await expect(boundedTransformText(body)).rejects.toBeInstanceOf(error);
  expect(cancel).toHaveBeenCalledOnce();
  expect(body.locked).toBe(false);
});

it("does not take ownership of an already locked stream", async () => {
  const body = streamOf([]);
  const reader = body.getReader();
  try {
    await expect(boundedTransformText(body)).rejects.toBeInstanceOf(TypeError);
    expect(body.locked).toBe(true);
  } finally {
    reader.releaseLock();
  }
});
