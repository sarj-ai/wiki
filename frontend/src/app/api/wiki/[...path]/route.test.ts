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
  it.each([
    "sidebar/drives/{id}/folders",
    "sidebar/drives/{id}/permissions",
    "sidebar/folders/{id}/files",
    "files/{id}",
    "aliases/files/{id}",
  ])("keeps the existing ASCII ID boundary for %s", async (template) => {
    fetchMock.mockResolvedValue(Response.json({}));
    const allowed = template.replace("{id}", "a".repeat(128));
    const response = await GET(request(allowed), context(allowed));
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    fetchMock.mockClear();
    const rejected = await Promise.all(
      ["a".repeat(129), "💼".repeat(128), "\u{D800}".repeat(128)].map(async (id) => {
        const path = template.replace("{id}", () => id);
        return await GET(request(path), context(path));
      }),
    );
    expect(rejected.map((rejectedResponse) => rejectedResponse.status)).toEqual([404, 404, 404]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("accepts the longest allowlisted path and preserves authenticated streaming", async () => {
    const path = `sidebar/drives/${"a".repeat(128)}/permissions`;
    expect(path.length).toBe(155);
    fetchMock.mockResolvedValue(Response.json({ permissions: [] }));
    const response = await GET(request(path), context(path));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[0]).toContain(path);
  });

  it("rejects an over-contract path before joining segments or looking up credentials", async () => {
    const path = ["files", "a".repeat(1024 * 1024)];
    const join = vi.spyOn(path, "join");
    cookiesMock.mockClear();
    const response = await GET(request("files/oversize"), {
      params: Promise.resolve({ path }),
    });
    expect(response.status).toBe(404);
    expect(join).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each(["auth/me", "sidebar/drives", "files/recent"])(
    "keeps failed %s reads private and traceable without exposing the upstream body",
    async (path) => {
      const requestId = "0682fc60-2dcd-4786-a99b-0f419fd7ab68";
      fetchMock.mockResolvedValue(
        Response.json({ detail: "private provider error" }, { status: 401 }),
      );
      const response = await GET(request(path, { "x-request-id": requestId }), context(path));
      expect(response.status).toBe(401);
      expect(response.headers.get("cache-control")).toBe("private, no-store");
      expect(response.headers.get("x-request-id")).toBe(requestId);
      expect(response.headers.get("location")).toBeNull();
      await expect(response.json()).resolves.toEqual({ detail: "Your session has expired" });
    },
  );

  it("traces an unauthenticated read without sending a backend request", async () => {
    MOCKS.token = "";
    const response = await GET(request("sidebar/drives"), context("sidebar/drives"));
    expect(response.status).toBe(401);
    expect(response.headers.get("x-request-id")).toMatch(/^[0-9a-f-]{36}$/u);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([401, 429, 503])(
    "owns separate failure bodies and trace headers for %i",
    async (status) => {
      const upstream = { detail: "Private provider failure" };
      fetchMock
        .mockResolvedValueOnce(Response.json(upstream, { status }))
        .mockResolvedValueOnce(Response.json(upstream, { status }));
      const first = await GET(
        request("sidebar/drives", { "x-request-id": "failure-first" }),
        context("sidebar/drives"),
      );
      const second = await GET(
        request("sidebar/drives", { "x-request-id": "failure-second" }),
        context("sidebar/drives"),
      );

      expect(first).not.toBe(second);
      expect(first.body).not.toBe(second.body);
      expect(first.headers).not.toBe(second.headers);
      expect(first.headers.get("x-request-id")).toBe("failure-first");
      expect(second.headers.get("x-request-id")).toBe("failure-second");
      first.headers.set("x-only-first", "1");
      expect(second.headers.get("x-only-first")).toBeNull();
      const body: unknown = await first.json();
      await expect(second.json()).resolves.toEqual(body);
      expect(JSON.stringify(body)).not.toContain("Private provider failure");
    },
  );
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

  it("forwards recent-page IDs in order through one private authenticated read", async () => {
    fetchMock.mockResolvedValue(Response.json({ pages: [] }));

    const response = await GET(
      request("files/recent?file_ids=doc-2&file_ids=doc-1&file_ids=doc-2"),
      context("files/recent"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0] ?? [];
    expect(url).toContain("/files/recent?file_ids=doc-2&file_ids=doc-1&file_ids=doc-2");
    expect(options?.headers).toMatchObject({ authorization: "Bearer private-session-token" });
    await expect(response.json()).resolves.toEqual({ pages: [] });
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
