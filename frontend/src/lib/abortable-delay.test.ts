import { abortableDelay } from "@/lib/abortable-delay";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("polling waits", () => {
  it("does not retain cancellation listeners after repeated polls", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const addListener = vi.spyOn(controller.signal, "addEventListener");
    const removeListener = vi.spyOn(controller.signal, "removeEventListener");
    const waits = Array.from(
      { length: 50 },
      async () => await abortableDelay(1, controller.signal),
    );

    await vi.runAllTimersAsync();
    await Promise.all(waits);

    expect(addListener).toHaveBeenCalledTimes(50);
    expect(removeListener).toHaveBeenCalledTimes(50);
  });

  it("stops an active polling wait immediately", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const pending = abortableDelay(1_000, controller.signal);
    const result = pending.catch((error: unknown) => error);

    controller.abort();

    await expect(result).resolves.toMatchObject({ name: "AbortError" });
  });

  it("does not schedule another wait after cancellation", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(abortableDelay(1_000, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});
