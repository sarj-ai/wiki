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
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
