// @vitest-environment happy-dom

import { deduplicatedRefresh } from "@/lib/session-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("session refresh classification", () => {
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
