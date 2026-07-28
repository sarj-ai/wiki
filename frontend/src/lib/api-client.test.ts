import { afterEach, describe, expect, it, vi } from "vitest";
import { ZodError } from "zod";

import { ApiError, wikiApi } from "@/lib/wiki-api";

const RAW_NODE = {
  id: "n1",
  mime_type: "text/plain",
  modified_time: null,
  name: "Doc",
  parent_id: null,
  shortcut_inaccessible: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    status,
  });
}

describe("response parsing through the transform schemas", () => {
  it("camelizes and collapses null to undefined", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([RAW_NODE])));

    const out = await wikiApi.getFolderFiles("folder-1");

    expect(out).toEqual([
      {
        id: "n1",
        mimeType: "text/plain",
        modifiedTime: undefined,
        name: "Doc",
        parentId: undefined,
        shortcutInaccessible: false,
        shortcutTargetDriveId: undefined,
        shortcutTargetDriveName: undefined,
        shortcutTargetId: undefined,
        shortcutTargetMimeType: undefined,
      },
    ]);
  });

  it("rejects with ZodError when the payload drifts from the contract", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([{ id: 123, name: "Eng" }])));

    await expect(wikiApi.getDrives()).rejects.toBeInstanceOf(ZodError);
  });
});

describe("request bodies", () => {
  it("sends the explicitly mapped snake_case wire shape", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      expect(JSON.parse(await request.text())).toEqual({
        mime_type: "application/vnd.google-apps.document",
        name: "RFC: Unify SIP",
        parent_folder_id: "1kl4",
      });
      return jsonResponse({
        drive_id: "d1",
        id: "f1",
        mime_type: "application/vnd.google-apps.document",
        modified_time: null,
        name: "RFC: Unify SIP",
        parent_folder_id: "1kl4",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await wikiApi.createFile({
      mimeType: "application/vnd.google-apps.document",
      name: "RFC: Unify SIP",
      parentFolderId: "1kl4",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(out.parentFolderId).toBe("1kl4");
    expect(out.modifiedTime).toBeUndefined();
  });
});

describe("error mapping", () => {
  it("surfaces the backend detail string as the ApiError message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "File not found" }, 404)),
    );

    await expect(wikiApi.getFile("gone")).rejects.toMatchObject({
      message: "File not found",
      name: "ApiError",
      status: 404,
    });
  });

  it("picks the first msg from FastAPI validation-error arrays", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(jsonResponse({ detail: [{ loc: [], msg: "field required" }] }, 422)),
    );

    await expect(wikiApi.getDrives()).rejects.toMatchObject({ message: "field required" });
  });

  it("falls back to the status code when the body has no detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("boom", { status: 502 })));

    const failure = await wikiApi.getDrives().catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).message).toBe("API error: 502");
  });
});

describe("401 refresh-and-replay", () => {
  it("refreshes once, replays the original request, and resolves", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (input: Request | string | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      calls.push(url);
      if (calls.length === 1) return Promise.resolve(jsonResponse({ detail: "expired" }, 401));
      if (url.endsWith("/auth/refresh"))
        return Promise.resolve(new Response(null, { status: 200 }));
      return Promise.resolve(jsonResponse([{ id: "d1", name: "Engineering" }]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await wikiApi.getDrives();

    expect(out).toEqual([{ id: "d1", name: "Engineering" }]);
    expect(calls).toHaveLength(3);
    expect(calls[1]).toMatch(/\/auth\/refresh$/);
  });

  it("replays a bodied request from the stashed clone (stream already consumed)", async () => {
    const bodies: (string | undefined)[] = [];
    const fetchMock = vi.fn(async (input: Request | string | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (input instanceof Request && !url.endsWith("/auth/refresh")) {
        bodies.push(await input.text());
      }
      if (fetchMock.mock.calls.length === 1) return jsonResponse({ detail: "expired" }, 401);
      if (url.endsWith("/auth/refresh")) return new Response(null, { status: 200 });
      return jsonResponse({
        drive_id: null,
        id: "f1",
        mime_type: "text/plain",
        modified_time: null,
        name: "Renamed",
        parent_folder_id: null,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await wikiApi.renameFile("f1", "Renamed");

    expect(out.name).toBe("Renamed");
    expect(bodies).toEqual(['{"name":"Renamed"}', '{"name":"Renamed"}']);
  });

  it("does not refresh when /auth/refresh itself returns 401", async () => {
    const fetchMock = vi.fn(async (input: Request | string | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      expect(url.endsWith("/auth/refresh")).toBe(false);
      return Promise.resolve(jsonResponse({ detail: "expired" }, 401));
    });
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401));
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(wikiApi.getDrives()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
