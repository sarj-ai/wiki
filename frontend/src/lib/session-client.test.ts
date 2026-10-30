// @vitest-environment happy-dom

import {
  captureBrowserSession,
  claimBrowserSession,
  retireBrowserSession,
} from "@/lib/browser-session";
import { deduplicatedRefresh } from "@/lib/session-client";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("session refresh classification", () => {
  it("shares one session listener and releases discarded callers before refresh settles", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const session = captureBrowserSession();
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const removed = vi.spyOn(session, "removeEventListener");
    const added = vi.spyOn(session, "addEventListener");
    const controllers = Array.from({ length: 100 }, () => new AbortController());
    const callerRemovals = controllers.map((controller) =>
      vi.spyOn(controller.signal, "removeEventListener"),
    );
    const discarded = controllers.map(
      async (controller) =>
        await deduplicatedRefresh(controller.signal, session).catch((error: unknown) => error),
    );
    const retained = deduplicatedRefresh(undefined, session);
    try {
      for (const controller of controllers) controller.abort();
      await expect(Promise.all(discarded)).resolves.toHaveLength(100);
      // Happy DOM also exposes AbortSignal.any's one shared network listener.
      expect(added).toHaveBeenCalledTimes(2);
      expect(removed).not.toHaveBeenCalled();
      expect(
        callerRemovals.map((spy) => spy.mock.calls.some(([event]) => event === "abort")),
      ).toEqual(Array.from({ length: 100 }, () => true));
      expect(fetchMock).toHaveBeenCalledOnce();
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
    } finally {
      response.resolve(new Response(null));
      await expect(retained).resolves.toBe("refreshed");
      expect(removed).toHaveBeenCalledOnce();
      retireBrowserSession(owner);
    }
  });

  it("rejects every retained waiter on session retirement before fetch settles", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const session = captureBrowserSession();
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const added = vi.spyOn(session, "addEventListener");
    const removed = vi.spyOn(session, "removeEventListener");
    const controllers = Array.from({ length: 100 }, () => new AbortController());
    const waiters = controllers.map(
      async (controller) =>
        await deduplicatedRefresh(controller.signal, session).catch((error: unknown) => error),
    );
    retireBrowserSession(owner);
    const results = await Promise.all(waiters);
    expect(results).toHaveLength(100);
    for (const result of results) expect(result).toMatchObject({ name: "AbortError" });
    expect(added).toHaveBeenCalledTimes(2);
    expect(removed).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    response.resolve(new Response(null));
  });

  it("retires the shared refresh without reusing it or clearing a new owner's pending refresh", async () => {
    const previous = {};
    const next = {};
    claimBrowserSession(previous);
    const oldSession = captureBrowserSession();
    const oldResponse = Promise.withResolvers<Response>();
    const nextResponse = Promise.withResolvers<Response>();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(oldResponse.promise)
      .mockReturnValueOnce(nextResponse.promise);
    vi.stubGlobal("fetch", fetchMock);
    const oldWaiter = deduplicatedRefresh(undefined, oldSession).catch((error: unknown) => error);
    retireBrowserSession(previous);
    claimBrowserSession(next);
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    await expect(oldWaiter).resolves.toMatchObject({ name: "AbortError" });
    const firstNewWaiter = deduplicatedRefresh();
    oldResponse.resolve(new Response(null, { status: 401 }));
    await Promise.resolve();
    await Promise.resolve();
    const secondNewWaiter = deduplicatedRefresh();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[1]?.signal?.aborted).toBe(false);
    nextResponse.resolve(new Response(null));
    await expect(Promise.all([firstNewWaiter, secondNewWaiter])).resolves.toEqual([
      "refreshed",
      "refreshed",
    ]);
    await expect(deduplicatedRefresh(undefined, oldSession)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    retireBrowserSession(next);
  });

  it("drops unused refresh bodies without waiting and prohibits cached/redirected recovery", async () => {
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const body = new ReadableStream<Uint8Array>({ cancel });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    vi.stubGlobal("fetch", fetchMock);
    try {
      await expect(deduplicatedRefresh()).resolves.toBe("refreshed");
      expect(cancel).toHaveBeenCalledOnce();
      expect(fetchMock.mock.calls[0]?.[1]?.cache).toBe("no-store");
      expect(fetchMock.mock.calls[0]?.[1]?.redirect).toBe("error");
    } finally {
      cleanup.resolve(undefined);
    }
  });
  it.each([
    [200, "refreshed"],
    [401, "unauthorized"],
    [429, "unavailable"],
    [503, "unavailable"],
  ] as const)("maps HTTP %s to %s", async (status, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));

    await expect(deduplicatedRefresh()).resolves.toBe(expected);
  });

  it("treats an opaque transport failure as unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(deduplicatedRefresh()).resolves.toBe("unavailable");
  });

  it("deduplicates concurrent refresh requests", async () => {
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);

    const first = deduplicatedRefresh();
    const second = deduplicatedRefresh();
    response.resolve(new Response(null, { status: 200 }));

    await expect(Promise.all([first, second])).resolves.toEqual(["refreshed", "refreshed"]);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("cancels a discarded waiter without cancelling another waiter or the shared refresh", async () => {
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const discarded = deduplicatedRefresh(controller.signal);
    const retained = deduplicatedRefresh();
    const discardedResult = discarded.catch((error: unknown) => error);

    controller.abort();

    await expect(discardedResult).resolves.toMatchObject({ name: "AbortError" });
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
    response.resolve(new Response(null, { status: 200 }));
    await expect(retained).resolves.toBe("refreshed");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("does not start a refresh for an already cancelled waiter", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(deduplicatedRefresh(controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("removes cancellation listeners after the shared request settles", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(null)));
    const controller = new AbortController();
    const removed = vi.spyOn(controller.signal, "removeEventListener");

    await expect(deduplicatedRefresh(controller.signal)).resolves.toBe("refreshed");
    expect(removed).toHaveBeenCalledOnce();
  });
});
