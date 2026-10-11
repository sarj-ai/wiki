const DELIVERY = vi.hoisted(() => vi.fn());

vi.mock("@/lib/clients/send-performance-event", () => ({ sendPerformanceEvent: DELIVERY }));

const SAMPLED_ID = "00000000-0000-4000-8000-000000000001";
const NEXT_ID = "00000000-0000-4000-8000-000000000002";
const UNSAMPLED_ID = "ff000000-0000-4000-8000-000000000001";

describe("navigation performance samples", () => {
  let frames: FrameRequestCallback[];
  let sampleId: ReturnType<Crypto["randomUUID"]>;

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    DELIVERY.mockClear();
    frames = [];
    window.localStorage.removeItem("sarj-perf");
    sampleId = SAMPLED_ID;
    vi.spyOn(crypto, "randomUUID").mockImplementation(() => sampleId);
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn<(callback: FrameRequestCallback) => number>((callback) => {
        frames.push(callback);
        return frames.length;
      }),
    );
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.localStorage.removeItem("sarj-perf");
    Reflect.deleteProperty(window, "__sarjPerf");
  });

  function frame() {
    const callback = frames.shift();
    expect(callback).toBeDefined();
    callback?.(performance.now());
  }

  it("does not read the clock for unsampled direct marks with no debug probe", async () => {
    sampleId = UNSAMPLED_ID;
    const { markNav } = await import("@/lib/perf-marks");
    const now = vi.spyOn(performance, "now").mockReturnValue(10);
    markNav("document", "click");
    now.mockClear();
    for (let index = 0; index < 100; index++) {
      markNav("document", "commit");
      markNav("document", "iframeMount");
      markNav("document", "iframeLoad");
    }
    expect(now).not.toHaveBeenCalled();
    expect(DELIVERY).not.toHaveBeenCalled();
  });

  it("still records direct unsampled marks when the debug probe is enabled", async () => {
    window.localStorage.setItem("sarj-perf", "1");
    sampleId = UNSAMPLED_ID;
    const { markNav } = await import("@/lib/perf-marks");
    const now = vi.spyOn(performance, "now").mockReturnValue(42);
    markNav("document", "commit");
    expect(now).toHaveBeenCalledOnce();
    expect(window.__sarjPerf).toEqual([{ extra: null, id: "document", phase: "commit", t: 42 }]);
    expect(DELIVERY).not.toHaveBeenCalled();
  });

  it("does not schedule paint callbacks for an unsampled navigation", async () => {
    sampleId = UNSAMPLED_ID;
    const { markNav, markPaint } = await import("@/lib/perf-marks");

    markNav("document", "click");
    markPaint("document", "toolbarPaint");

    expect(frames).toHaveLength(0);
    expect(DELIVERY).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("retains the double-frame paint measurement for a sampled navigation", async () => {
    const { markNav, markPaint } = await import("@/lib/perf-marks");
    const now = vi.spyOn(performance, "now").mockReturnValue(10);
    markNav("document", "click");
    markPaint("document", "toolbarPaint");
    frame();
    expect(DELIVERY).not.toHaveBeenCalled();
    now.mockReturnValue(90);
    frame();

    expect(DELIVERY).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ durationMs: 80, navigationId: SAMPLED_ID, phase: "toolbarPaint" }),
    );
  });

  it.each([false, true])(
    "does not attribute a previous navigation's paint to a replacement (first frame ran: %s)",
    async (firstFrameRan) => {
      const { markNav, markPaint } = await import("@/lib/perf-marks");
      markNav("document", "click");
      markPaint("document", "toolbarPaint");
      if (firstFrameRan) frame();
      sampleId = NEXT_ID;
      markNav("document", "click");
      frame();

      expect(frames).toHaveLength(0);
      expect(DELIVERY).not.toHaveBeenCalled();
      markPaint("document", "toolbarPaint");
      frame();
      frame();
      expect(DELIVERY).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ navigationId: NEXT_ID, phase: "toolbarPaint" }),
      );
    },
  );

  it("stops paint callbacks when the sample completed or expired before its first frame", async () => {
    const { markNav, markPaint } = await import("@/lib/perf-marks");
    markNav("completed", "click");
    markPaint("completed", "toolbarPaint");
    markNav("completed", "iframeLoad");
    frame();
    expect(frames).toHaveLength(0);
    expect(DELIVERY).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);

    markNav("expired", "click");
    markPaint("expired", "toolbarPaint");
    vi.advanceTimersByTime(120_000);
    frame();
    expect(frames).toHaveLength(0);
    expect(DELIVERY).toHaveBeenCalledOnce();
  });

  it("sends each phase once per navigation and retains its first observed duration", async () => {
    const { markNav } = await import("@/lib/perf-marks");
    const now = vi.spyOn(performance, "now").mockReturnValue(10);
    markNav("document", "click");
    now.mockReturnValue(20);
    for (const phase of ["commit", "toolbarPaint", "iframeMount"]) {
      markNav("document", phase);
      markNav("document", phase);
    }
    now.mockReturnValue(30);
    markNav("document", "toolbarPaint");
    markNav("document", "iframeLoad");
    markNav("document", "iframeLoad");

    expect(DELIVERY).toHaveBeenCalledTimes(4);
    expect(DELIVERY).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ durationMs: 10, navigationId: SAMPLED_ID, phase: "toolbarPaint" }),
    );
    expect(vi.getTimerCount()).toBe(0);
    sampleId = NEXT_ID;
    markNav("document", "click");
    markNav("document", "toolbarPaint");
    expect(DELIVERY).toHaveBeenLastCalledWith(
      expect.objectContaining({ durationMs: 0, navigationId: NEXT_ID, phase: "toolbarPaint" }),
    );
    expect(DELIVERY).toHaveBeenCalledTimes(5);
  });

  it("keeps the opt-in paint probe active for an unsampled navigation", async () => {
    window.localStorage.setItem("sarj-perf", "1");
    sampleId = UNSAMPLED_ID;
    const { markNav, markPaint } = await import("@/lib/perf-marks");
    markNav("document", "click");
    markPaint("document", "toolbarPaint");
    frame();
    frame();

    expect(window.__sarjPerf).toEqual([
      expect.objectContaining({ id: "document", phase: "click" }),
      expect.objectContaining({ id: "document", phase: "toolbarPaint" }),
    ]);
    expect(DELIVERY).not.toHaveBeenCalled();
  });
});
