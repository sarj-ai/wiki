import { boundedErrorText } from "@/lib/bounded-error-text";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("bounded error bodies", () => {
  it("accepts exactly 32 KiB of valid multi-byte UTF-8 guidance", async () => {
    const text = "é".repeat(16 * 1024);
    await expect(
      boundedErrorText(new Response(text).body, new AbortController().signal),
    ).resolves.toBe(text);
  });

  it("stops a stalled error body after ten seconds without awaiting slow cleanup", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(() => {
        controller.abort(new DOMException("Deadline exceeded", "TimeoutError"));
      }, milliseconds);
      return controller.signal;
    });
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{"));
      },
      cancel,
    });
    let settled = false;
    const controller = new AbortController();
    const pending = (async () => {
      try {
        return await boundedErrorText(body, controller.signal);
      } catch (error: unknown) {
        settled = true;
        return error;
      }
    })();
    try {
      await vi.advanceTimersByTimeAsync(9_999);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toMatchObject({ name: "TimeoutError" });
      expect(cancel).toHaveBeenCalledOnce();
    } finally {
      cleanup.resolve(undefined);
      controller.abort();
      await pending;
    }
  });
});
