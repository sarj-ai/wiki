import { afterEach, describe, expect, it, vi } from "vitest";
import { ZodError } from "zod";

import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";
import { ApiError, makeWikiApi } from "@/lib/wiki-api";

const wikiApi = makeWikiApi();

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
  return Response.json(body, {
    headers: { "content-type": "application/json" },
    status,
  });
}

describe("response parsing through the transform schemas", () => {
  it("identifies the personal drive without changing shared-drive defaults", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse([
          { id: "personal-root", is_personal: true, name: "My Drive" },
          { id: "engineering", name: "Engineering" },
        ]),
      ),
    );

    const out = await wikiApi.getDrives();

    expect(out).toEqual([
      { id: "personal-root", isPersonal: true, name: "My Drive" },
      { id: "engineering", isPersonal: false, name: "Engineering" },
    ]);
  });

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

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(out).toMatchObject({ parentFolderId: "1kl4", modifiedTime: undefined });
  });
});

describe("error mapping", () => {
  it("retires error decoding without aborting a dispatched mutation", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{"));
      },
      cancel,
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);
    const pending = wikiApi.renameFile("file-1", "Retired title").catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(body.locked).toBe(true);
    });
    const request = fetchMock.mock.calls[0]?.[0];
    if (!(request instanceof Request))
      throw new TypeError("Expected the generated mutation request");
    retireBrowserSession(owner);
    expect(request.signal.aborted).toBe(false);
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("stops reading oversized API errors and preserves the HTTP fallback", async () => {
    const encoded = new TextEncoder().encode(JSON.stringify({ detail: "x".repeat(1024 * 1024) }));
    let received = 0;
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          if (received === encoded.byteLength) {
            controller.close();
            return;
          }
          const chunk = encoded.subarray(received, received + 8192);
          received += chunk.byteLength;
          controller.enqueue(chunk);
        },
        cancel,
      },
      { highWaterMark: 0 },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 502 })),
    );
    const failure = await wikiApi.getFile("error-body").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    if (!(failure instanceof ApiError)) throw new Error("Expected ApiError");
    expect(failure.message.length).toBe("API error: 502".length);
    expect(received).toBe(40_960);
    expect(cancel).toHaveBeenCalledOnce();
  });

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
    if (!(failure instanceof ApiError)) throw new Error("Expected ApiError");
    expect(failure.message).toBe("API error: 502");
  });

  it("uses stable copy for a bodyless perimeter rate limit", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 429 })));

    await expect(wikiApi.getDrives()).rejects.toMatchObject({
      message: "Too many requests. Try again in a minute.",
      status: 429,
    });
  });

  it("releases a discarded rate-limit body without waiting for stream cleanup", async () => {
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn(async () => await cleanup.promise);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }), { status: 429 })),
    );

    await expect(wikiApi.getDrives()).rejects.toMatchObject({
      message: "Too many requests. Try again in a minute.",
      status: 429,
    });
    expect(cancel).toHaveBeenCalledOnce();
    cleanup.resolve(undefined);
  });
});

describe("401 refresh-and-replay", () => {
  it("captures ownership before asynchronous SDK work can dispatch a mutation", async () => {
    const previous = {};
    const replacement = {};
    claimBrowserSession(previous);
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    const pending = wikiApi.renameFile("file-1", "Old draft").catch((error: unknown) => error);
    retireBrowserSession(previous);
    claimBrowserSession(replacement);
    try {
      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      retireBrowserSession(replacement);
    }
  });

  it("lets a dispatched mutation finish without refreshing, replaying, or notifying its replacement", async () => {
    const previous = {};
    const replacement = {};
    claimBrowserSession(previous);
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const listener = vi.fn<() => void>();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    try {
      const pending = wikiApi.renameFile("file-1", "Old draft").catch((error: unknown) => error);
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalledOnce();
      });
      const request = fetchMock.mock.calls[0]?.[0];
      if (!(request instanceof Request))
        throw new TypeError("Expected the generated mutation request");
      retireBrowserSession(previous);
      claimBrowserSession(replacement);
      expect(request.signal.aborted).toBe(false);
      response.resolve(jsonResponse({ detail: "old session expired" }, 401));

      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(fetchMock).toHaveBeenCalledOnce();
      expect(listener).not.toHaveBeenCalled();
    } finally {
      retireBrowserSession(replacement);
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    }
  });

  it("cancels an unsignalled generated read when its provider retires", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const pending = wikiApi.getFile("file-1").catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });
    const request = fetchMock.mock.calls[0]?.[0];
    if (!(request instanceof Request)) throw new TypeError("Expected the generated read request");
    retireBrowserSession(owner);
    expect(request.signal.aborted).toBe(true);
    response.resolve(jsonResponse({ detail: "old session expired" }, 401));

    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("does not replay a generated mutation whose refresh owner retires", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const refresh = Promise.withResolvers<Response>();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "old session expired" }, 401))
      .mockReturnValueOnce(refresh.promise);
    vi.stubGlobal("fetch", fetchMock);
    const pending = wikiApi.renameFile("file-1", "Old draft").catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    const mutationRequest = fetchMock.mock.calls[0]?.[0];
    if (!(mutationRequest instanceof Request))
      throw new TypeError("Expected the generated mutation request");
    retireBrowserSession(owner);
    expect(mutationRequest.signal.aborted).toBe(false);
    expect(fetchMock.mock.calls[1]?.[1]?.signal?.aborted).toBe(true);
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    refresh.resolve(new Response(null));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("cancels a discarded generated read waiting for refresh without replaying it", async () => {
    const refresh = Promise.withResolvers<Response>();
    const cancel = vi.fn();
    const expired = new Response(new ReadableStream({ cancel }), { status: 401 });
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : "href" in input ? input.href : input.url;
      return url.endsWith("/auth/refresh") ? await refresh.promise : expired;
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const pending = wikiApi.getFile("file-1", controller.signal);
    const outcome = pending.catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
    controller.abort();
    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
    refresh.resolve(new Response(null));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("releases a retired mutation replay body without cancelling its dispatched write", async () => {
    const owner = {};
    const replacement = {};
    claimBrowserSession(owner);
    const replay = Promise.withResolvers<Response>();
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn(async () => await cleanup.promise);
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(new Response(null))
      .mockReturnValueOnce(replay.promise);
    vi.stubGlobal("fetch", fetchMock);
    const listener = vi.fn<() => void>();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    try {
      const pending = wikiApi.renameFile("file-1", "Old draft").catch((error: unknown) => error);
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalledTimes(3);
      });
      const request = fetchMock.mock.calls[2]?.[0];
      if (!(request instanceof Request))
        throw new TypeError("Expected the replay mutation request");
      retireBrowserSession(owner);
      claimBrowserSession(replacement);
      expect(request.signal.aborted).toBe(false);
      replay.resolve(new Response(new ReadableStream({ cancel })));

      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(cancel).toHaveBeenCalledOnce();
      expect(fetchMock).toHaveBeenCalledTimes(3);
      expect(listener).not.toHaveBeenCalled();
    } finally {
      cleanup.resolve(undefined);
      retireBrowserSession(replacement);
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    }
  });

  it("refreshes once, replays the original request, and resolves", async () => {
    const calls: string[] = [];
    const fetchMock = vi.fn((input: Request | string | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      calls.push(url);
      if (calls.length === 1) return jsonResponse({ detail: "expired" }, 401);
      if (url.endsWith("/auth/refresh")) return new Response(null, { status: 200 });
      return jsonResponse([{ id: "d1", name: "Engineering" }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const out = await wikiApi.getDrives();

    expect(out).toEqual([{ id: "d1", isPersonal: false, name: "Engineering" }]);
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
    const listener = vi.fn();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    const fetchMock = vi.fn((input: Request | string | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      expect(url.endsWith("/auth/refresh")).toBe(false);
      return jsonResponse({ detail: "expired" }, 401);
    });
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401));
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(wikiApi.getDrives()).rejects.toMatchObject({ message: "expired", status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
  });

  it("does not announce terminal expiry when refresh is unavailable", async () => {
    const listener = vi.fn();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    const fetchMock = vi.fn((input: Request | string | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith("/auth/refresh")) return new Response(null, { status: 429 });
      return jsonResponse({ detail: "expired" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(wikiApi.getDrives()).rejects.toMatchObject({
      message: "Your session could not be refreshed. Try again in a moment.",
      status: 503,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(listener).not.toHaveBeenCalled();
    window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
  });
});
