// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

const MOCKS = vi.hoisted(() => ({ token: "private-session-token" }));
const fetchMock = vi.fn<typeof fetch>();
const cookiesMock = vi.hoisted(() =>
  vi.fn<() => Promise<{ get: () => { value: string } | undefined }>>(),
);

vi.mock("next/headers", () => ({ cookies: cookiesMock }));

function request(path: string, headers: Record<string, string> = {}): Request {
  const value = new Request(`https://wiki.sarj.ai/api/wiki/${path}`, {
    headers: { "x-wiki-read": "1", "sec-fetch-site": "same-origin", ...headers },
  });
  // happy-dom's Request constructor drops browser-controlled Sec-Fetch fields.
  for (const [key, header] of Object.entries(headers)) value.headers.set(key, header);
  return value;
}

function context(path: string) {
  return { params: Promise.resolve({ path: path.split("/") }) };
}

beforeEach(() => {
  MOCKS.token = "private-session-token";
  cookiesMock.mockResolvedValue({ get: () => (MOCKS.token ? { value: MOCKS.token } : undefined) });
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.restoreAllMocks());

describe("authenticated parallel wiki reads", () => {
  it.each(["disconnect", "deadline"] as const)(
    "discards a late successful upstream body after %s without exposing it",
    async (reason) => {
      const caller = new AbortController();
      const deadline = new AbortController();
      vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
      const headers = Promise.withResolvers<Response>();
      const cleanup = Promise.withResolvers<undefined>();
      const cancelled = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
      fetchMock.mockReturnValue(headers.promise);
      const result = GET(
        new Request("https://wiki.sarj.ai/api/wiki/files/doc-1", {
          headers: { "x-wiki-read": "1" },
          signal: caller.signal,
        }),
        context("files/doc-1"),
      );
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
      if (reason === "disconnect") caller.abort();
      else deadline.abort(new DOMException("The operation timed out", "TimeoutError"));
      headers.resolve(new Response(new ReadableStream<Uint8Array>({ cancel: cancelled })));
      try {
        const response = await result;
        expect(response.status).toBe(502);
        await expect(response.json()).resolves.toEqual({
          detail: "The request could not be completed",
        });
        expect(cancelled).toHaveBeenCalledOnce();
        expect(fetchMock).toHaveBeenCalledOnce();
      } finally {
        cleanup.resolve(undefined);
      }
    },
  );

  it("reaches the backend with an expired session under Cloudflare redirect semantics", async () => {
    const unauthorized = Promise.resolve(
      Response.json({ detail: "private provider error" }, { status: 401 }),
    );
    fetchMock.mockImplementation(async (_input, init) => {
      if (init?.redirect === "error") throw new TypeError("Invalid redirect value");
      return await unauthorized;
    });
    const response = await GET(request("sidebar/drives"), context("sidebar/drives"));
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ detail: "Your session has expired" });
  });

  it("redacts an upstream redirect instead of following it", async () => {
    fetchMock.mockResolvedValue(
      new Response("private redirect body", {
        headers: { location: "https://other-origin.test/private" },
        status: 302,
      }),
    );
    const response = await GET(request("sidebar/drives"), context("sidebar/drives"));
    expect(response.status).toBe(502);
    expect(response.headers.get("location")).toBeNull();
    await expect(response.json()).resolves.toEqual({
      detail: "The request could not be completed",
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("preserves the HTTP failure without waiting for stream cleanup", async () => {
    const cleanup = Promise.withResolvers<undefined>();
    const body = new ReadableStream<Uint8Array>({ cancel: async () => await cleanup.promise });
    fetchMock.mockResolvedValue(new Response(body, { status: 429 }));
    try {
      const response = await GET(request("files/doc-1"), context("files/doc-1"));
      expect(response.status).toBe(429);
      await expect(response.json()).resolves.toEqual({
        detail: "Too many requests. Try again in a minute.",
      });
    } finally {
      cleanup.resolve(undefined);
    }
  });

  it("does not send an upstream request after the caller has already disconnected", async () => {
    const controller = new AbortController();
    controller.abort();
    const response = await GET(
      new Request("https://wiki.sarj.ai/api/wiki/files/doc-1", {
        headers: { "x-wiki-read": "1" },
        signal: controller.signal,
      }),
      context("files/doc-1"),
    );
    expect(response.status).toBe(502);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("streams authorized JSON and timing without exposing credentials or provider headers", async () => {
    fetchMock.mockResolvedValue(
      new Response('[{"id":"drive-1"}]', {
        headers: { "server-timing": "db;dur=2.1", "set-cookie": "provider=secret" },
      }),
    );
    const response = await GET(request("sidebar/drives"), context("sidebar/drives"));
    await expect(response.json()).resolves.toEqual([{ id: "drive-1" }]);
    expect(response.headers.get("server-timing")).toBe("db;dur=2.1");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("set-cookie")).toBeNull();
    const [url, options] = fetchMock.mock.calls[0] ?? [];
    expect(url).toContain("/sidebar/drives");
    expect(options?.redirect).toBe("manual");
    expect(options?.headers).toMatchObject({ authorization: "Bearer private-session-token" });
  });

  it.each([
    "auth/logout",
    "internal/tasks/run",
    "files/doc-1/content",
    "../health",
    "files/%2Fsecret",
    "sidebar/drives/a/refresh",
    "tasks/invalid-id",
    "templates/refresh",
  ])("rejects paths outside the read allowlist: %s", async (path) => {
    const response = await GET(request(path), context(path));
    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects unauthenticated and cross-site requests before fetching", async () => {
    const crossSite = await GET(
      request("sidebar/drives", { "sec-fetch-site": "same-site" }),
      context("sidebar/drives"),
    );
    expect(crossSite.status).toBe(403);
    MOCKS.token = "";
    const unauthorized = await GET(request("sidebar/drives"), context("sidebar/drives"));
    expect(unauthorized.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("redacts provider errors", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "private-token-and-document-name" }, { status: 403 }),
    );
    const response = await GET(request("files/doc-1"), context("files/doc-1"));
    expect(response.status).toBe(403);
    await expect(response.text()).resolves.not.toContain("private-token");
  });

  it("returns headers before a slow upstream response body finishes", async () => {
    let finishBody: (() => void) | undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("["));
        finishBody = () => {
          controller.enqueue(new TextEncoder().encode("]"));
          controller.close();
        };
      },
    });
    fetchMock.mockResolvedValue(new Response(body));

    const response = await GET(request("sidebar/drives"), context("sidebar/drives"));

    expect(response.status).toBe(200);
    expect(response.body).not.toBeNull();
    finishBody?.();
    await expect(response.json()).resolves.toEqual([]);
  });

  it("propagates client disconnects to the pending upstream read", async () => {
    const controller = new AbortController();
    const pendingFetch = Promise.withResolvers<Response>();
    fetchMock.mockReturnValue(pendingFetch.promise);
    const pendingResponse = GET(
      new Request("https://wiki.sarj.ai/api/wiki/sidebar/drives", {
        headers: { "x-wiki-read": "1" },
        signal: controller.signal,
      }),
      context("sidebar/drives"),
    );
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });

    controller.abort();

    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    pendingFetch.reject(new DOMException("The operation was aborted", "AbortError"));
    const response = await pendingResponse;
    expect(response.status).toBe(502);
  });

  it.each([
    "auth/me",
    "sidebar/drives",
    "sidebar/drives/drive-1/folders",
    "sidebar/drives/drive-1/permissions",
    "sidebar/folders/folder-1/files",
    "files/file-1",
    "aliases/files/file-1",
    "extras/preferences",
    "extras/folder-icons",
    "extras/doc-icons",
    "extras/sort-order",
    "drive-search",
    "drive-search/snippet",
    "tasks/55555555-5555-4555-8555-555555555555",
    "templates",
  ])("supports the authorized read family %s", async (path) => {
    fetchMock.mockResolvedValue(Response.json([]));

    const response = await GET(request(path), context(path));

    expect(response.status).toBe(200);
    const [url] = fetchMock.mock.calls[0] ?? [];
    expect(url).toContain(`/${path}`);
  });
});
