import { afterEach, describe, expect, it, vi } from "vitest";

const cookieValue = vi.hoisted(() => ({ token: undefined as string | undefined }));

vi.mock("next/headers", () => ({
  cookies: () =>
    Promise.resolve({
      get: (name: string) =>
        name === "access_token" && cookieValue.token !== undefined
          ? { name, value: cookieValue.token }
          : undefined,
    }),
}));

import { serverGet, ServerApiError } from "@/lib/server-api";

afterEach(() => {
  cookieValue.token = undefined;
  vi.unstubAllGlobals();
});

describe("serverGet", () => {
  it("fails fast with 401 when no access token cookie exists", async () => {
    await expect(serverGet("sidebar/drives")).rejects.toMatchObject({
      name: "ServerApiError",
      status: 401,
    });
  });

  it("forwards the cookie and camelizes exactly like the client parseJson", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify([
            { id: "f1", name: "Doc", mime_type: "text/plain", parent_id: null, nested: { a_b: 1 } },
          ]),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const out = await serverGet("sidebar/drives");
    expect(out).toEqual([
      { id: "f1", name: "Doc", mimeType: "text/plain", parentId: null, nested: { aB: 1 } },
    ]);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["cookie"]).toBe("access_token=jwt-token");
  });

  it("throws ServerApiError with the upstream status on non-OK", async () => {
    cookieValue.token = "jwt-token";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })));
    await expect(serverGet("files/gone")).rejects.toBeInstanceOf(ServerApiError);
    await expect(serverGet("files/gone")).rejects.toMatchObject({ status: 404 });
  });
});
