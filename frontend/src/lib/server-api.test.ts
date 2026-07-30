import { afterEach, describe, expect, it, vi } from "vitest";
import { ZodError } from "zod";

import { ServerApiError, serverWikiApi } from "@/lib/server-api";
import { cookieState } from "@/test-stubs/cookie-state";

afterEach(() => {
  cookieState.token = undefined;
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    status,
  });
}

describe("serverWikiApi", () => {
  it("fails fast with 401 when no access token cookie exists", async () => {
    await expect(serverWikiApi.getDrives()).rejects.toMatchObject({
      name: "ServerApiError",
      status: 401,
    });
  });

  it("forwards the cookie and parses through the endpoint transform", async () => {
    cookieState.token = "jwt-token";
    const fetchMock = vi.fn(async (request: Request) => {
      expect(request.headers.get("cookie")).toBe("access_token=jwt-token");
      return Promise.resolve(jsonResponse([{ icon: null, id: "f1", name: "Doc" }]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await serverWikiApi.listFolderIcons().catch((e: unknown) => e);
    expect(out).toBeInstanceOf(ZodError);

    fetchMock.mockResolvedValue(jsonResponse([{ folder_drive_id: "fd1", icon: null }]));
    const icons = await serverWikiApi.listFolderIcons();
    expect(icons).toEqual([{ folderDriveId: "fd1", icon: undefined }]);
  });

  it("throws ServerApiError with the upstream status on non-OK", async () => {
    cookieState.token = "jwt-token";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })));

    await expect(serverWikiApi.getFile("gone")).rejects.toBeInstanceOf(ServerApiError);
    await expect(serverWikiApi.getFile("gone")).rejects.toMatchObject({ status: 404 });
  });
});
