import { afterEach, describe, expect, it, vi } from "vitest";
import { ZodError } from "zod";

import { ServerApiError, serverWikiApi } from "@/lib/server-api";

const cookieValue = vi.hoisted<{ token: string | undefined }>(() => ({ token: undefined }));

vi.mock("next/headers", () => ({
  cookies: () => ({
    get: (name: string) =>
      name === "access_token" && cookieValue.token !== undefined
        ? { name, value: cookieValue.token }
        : undefined,
  }),
  headers: () => new Headers({ "X-Request-ID": "frontend-server-test" }),
}));

afterEach(() => {
  cookieValue.token = undefined;
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, {
    headers: { "content-type": "application/json" },
    status,
  });
}

describe("serverWikiApi", () => {
  it("rejects backend redirects without forwarding their destination", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(null, {
        headers: { location: "https://other-origin.test/private" },
        status: 307,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(serverWikiApi.getDrives()).rejects.toMatchObject({ status: 502 });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[1]?.redirect).toBe("manual");
  });

  it("releases an unused HTTP error body without waiting for cleanup", async () => {
    cookieValue.token = "jwt-token";
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const body = new ReadableStream<Uint8Array>({ cancel });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 403 })),
    );
    try {
      await expect(serverWikiApi.getFile("file-1")).rejects.toMatchObject({ status: 403 });
      expect(cancel).toHaveBeenCalledOnce();
    } finally {
      cleanup.resolve(undefined);
    }
  });
  it("fails fast with 401 when no access token cookie exists", async () => {
    await expect(serverWikiApi.getDrives()).rejects.toMatchObject({
      name: "ServerApiError",
      status: 401,
    });
  });

  it("forwards a Bearer token and parses through the endpoint transform", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi.fn((request: Request) => {
      expect(request.headers.get("authorization")).toBe("Bearer jwt-token");
      expect(request.headers.get("cookie")).toBeNull();
      expect(request.headers.get("x-request-id")).toBe("frontend-server-test");
      return jsonResponse([{ icon: null, id: "f1", name: "Doc" }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await serverWikiApi.listFolderIcons().catch((e: unknown) => e);
    expect(out).toBeInstanceOf(ZodError);

    fetchMock.mockResolvedValue(jsonResponse([{ folder_drive_id: "fd1", icon: null }]));
    const icons = await serverWikiApi.listFolderIcons();
    expect(icons).toEqual([{ folderDriveId: "fd1", icon: undefined }]);
  });

  it("throws ServerApiError with the upstream status on non-OK", async () => {
    cookieValue.token = "jwt-token";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })));

    await expect(serverWikiApi.getFile("gone")).rejects.toBeInstanceOf(ServerApiError);
    await expect(serverWikiApi.getFile("gone")).rejects.toMatchObject({ status: 404 });
  });
});
