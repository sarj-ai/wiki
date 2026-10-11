// @vitest-environment node

import { boundedText } from "@/lib/bounded-text";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("direct bounded text reads", () => {
  it("decodes multi-byte characters split across every byte boundary", async () => {
    const text = "Wiki العربية 😃";
    const bytes = new TextEncoder().encode(text);
    let index = 0;
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          if (index === bytes.length) controller.close();
          else controller.enqueue(bytes.subarray(index, ++index));
        },
      },
      { highWaterMark: 0 },
    );
    const pipeline = vi.spyOn(body, "pipeTo");
    await expect(boundedText(body, bytes.length)).resolves.toBe(text);
    expect(pipeline).not.toHaveBeenCalled();
    expect(body.locked).toBe(false);
  });

  it("keeps the exact byte boundary and does not decode an oversized chunk", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([97, 98, 99, 0xff]));
      },
      cancel,
    });
    await expect(boundedText(body, 3, undefined, "fixture limit")).rejects.toThrow(
      new RangeError("fixture limit"),
    );
    expect(cancel).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });

  it.each([{ values: [0xff] }, { values: [0xe2, 0x82] }])(
    "retains strict UTF-8 rejection for %j",
    async ({ values }) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(Uint8Array.from(values));
          controller.close();
        },
      });
      await expect(boundedText(body, 10)).rejects.toBeInstanceOf(TypeError);
      expect(body.locked).toBe(false);
    },
  );

  it("rejects a stalled read on abort without waiting for source cancellation", async () => {
    const controller = new AbortController();
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const body = new ReadableStream<Uint8Array>({ cancel }, { highWaterMark: 0 });
    const removed = vi.spyOn(controller.signal, "removeEventListener");
    const pending = boundedText(body, 10, controller.signal).catch((error: unknown) => error);
    try {
      controller.abort(new DOMException("fixture abort", "AbortError"));
      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(cancel).toHaveBeenCalledOnce();
      expect(removed).toHaveBeenCalledOnce();
      expect(body.locked).toBe(false);
    } finally {
      cleanup.resolve(undefined);
    }
  });

  it("preserves a stream error and releases its read lock and listener", async () => {
    const controller = new AbortController();
    const failure = new Error("fixture source failure");
    const body = new ReadableStream<Uint8Array>({
      start(stream) {
        stream.error(failure);
      },
    });
    const removed = vi.spyOn(controller.signal, "removeEventListener");
    await expect(boundedText(body, 10, controller.signal)).rejects.toBe(failure);
    expect(removed).toHaveBeenCalledOnce();
    expect(body.locked).toBe(false);
  });
});
