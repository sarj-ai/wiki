import { sendPerformanceEvent } from "@/lib/clients/send-performance-event";

const EVENT = {
  durationMs: 12,
  navigationId: "00000000-0000-4000-8000-000000000001",
  phase: "toolbarPaint",
  saveData: false,
  visibility: "visible",
} as const;

afterEach(() => vi.unstubAllGlobals());

describe("optional performance event delivery", () => {
  it("uses only the beacon when its queue accepts the event", () => {
    const beacon = vi.fn().mockReturnValue(true);
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("navigator", { sendBeacon: beacon });
    vi.stubGlobal("fetch", fetchMock);

    sendPerformanceEvent(EVENT);

    expect(beacon).toHaveBeenCalledExactlyOnceWith("/api/perf", expect.any(Blob));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([200, 503])(
    "discards the unused fallback HTTP %s body without waiting for cleanup or retrying",
    async (status) => {
      const cleanup = Promise.withResolvers<undefined>();
      const cancelled = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
      const fetchMock = vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response(new ReadableStream<Uint8Array>({ cancel: cancelled }), { status }),
        );
      vi.stubGlobal("navigator", { sendBeacon: vi.fn().mockReturnValue(false) });
      vi.stubGlobal("fetch", fetchMock);
      try {
        sendPerformanceEvent(EVENT);
        await Promise.resolve();
        await Promise.resolve();

        expect(cancelled).toHaveBeenCalledOnce();
        expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
          "/api/perf",
          expect.objectContaining({
            body: JSON.stringify(EVENT),
            keepalive: true,
            method: "POST",
          }),
        );
      } finally {
        cleanup.resolve(undefined);
      }
    },
  );
});
