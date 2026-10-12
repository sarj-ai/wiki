import { z } from "zod";

import { ApiError, RATE_LIMIT_MESSAGE, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";
import { wikiReadHttp } from "@/lib/clients/wiki-read-http";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";

const PayloadSchema = z.object({ value: z.string() });
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("authenticated wiki read recovery", () => {
  it("reports fixed rate-limit guidance without reading or awaiting an unused body", async () => {
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const body = new ReadableStream<Uint8Array>({ cancel });
    fetchMock.mockResolvedValue(new Response(body, { status: 429 }));
    try {
      await expect(wikiReadHttp("/files/first", PayloadSchema)).rejects.toEqual(
        new ApiError(429, RATE_LIMIT_MESSAGE),
      );
      expect(cancel).toHaveBeenCalledOnce();
      expect(fetchMock).toHaveBeenCalledOnce();
    } finally {
      cleanup.resolve(undefined);
    }
  });
  it("does not construct query parameters for reads with no defined query fields", async () => {
    const parameters = vi.spyOn(globalThis, "URLSearchParams");
    fetchMock
      .mockResolvedValueOnce(Response.json({ value: "fresh" }))
      .mockResolvedValueOnce(Response.json({ value: "fresh" }));
    await expect(wikiReadHttp("/auth/me", PayloadSchema)).resolves.toEqual({ value: "fresh" });
    await expect(wikiReadHttp("/auth/me", PayloadSchema, { ignored: undefined })).resolves.toEqual({
      value: "fresh",
    });
    expect(parameters).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/wiki/auth/me",
      "/api/wiki/auth/me",
    ]);
  });

  it("retains defined empty, zero and escaped query values", async () => {
    fetchMock.mockResolvedValue(Response.json({ value: "fresh" }));
    await wikiReadHttp("/drive-search", PayloadSchema, {
      q: "a b&c",
      empty: "",
      zero: 0,
      ignored: undefined,
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/wiki/drive-search?q=a+b%26c&empty=&zero=0");
  });
  it("stops and redacts an oversized BFF error body", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(32 * 1024 + 1));
      },
      cancel,
    });
    fetchMock.mockResolvedValue(new Response(body, { status: 502 }));
    await expect(wikiReadHttp("/files/first", PayloadSchema)).rejects.toMatchObject({
      message: "The request could not be completed",
      status: 502,
    });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("cancels error decoding when its caller leaves", async () => {
    const controller = new AbortController();
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(stream) {
        stream.enqueue(new TextEncoder().encode("{"));
      },
      cancel,
    });
    fetchMock.mockResolvedValue(new Response(body, { status: 502 }));
    const pending = wikiReadHttp("/files/first", PayloadSchema, {}, controller.signal).catch(
      (error: unknown) => error,
    );
    await vi.waitFor(() => {
      expect(body.locked).toBe(true);
    });
    controller.abort();
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("disposes a late replay response when the waiting caller has already left", async () => {
    const controller = new AbortController();
    const replay = Promise.withResolvers<Response>();
    const cancel = vi.fn();
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null))
      .mockReturnValueOnce(replay.promise);
    const pending = wikiReadHttp("/files/first", PayloadSchema, {}, controller.signal).catch(
      (error: unknown) => error,
    );
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });
    controller.abort();
    replay.resolve(new Response(new ReadableStream<Uint8Array>({ cancel })));
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("retires an unsignalled read without refreshing or notifying a replacement", async () => {
    const previous = {};
    const replacement = {};
    claimBrowserSession(previous);
    const response = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(response.promise);
    const listener = vi.fn<() => void>();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    try {
      const pending = wikiReadHttp("/files/old-user", PayloadSchema).catch(
        (error: unknown) => error,
      );
      retireBrowserSession(previous);
      claimBrowserSession(replacement);
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
      response.resolve(new Response(null, { status: 401 }));

      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(fetchMock).toHaveBeenCalledOnce();
      expect(listener).not.toHaveBeenCalled();
      fetchMock.mockResolvedValueOnce(Response.json({ value: "replacement" }));
      await expect(wikiReadHttp("/files/new-user", PayloadSchema)).resolves.toEqual({
        value: "replacement",
      });
      expect(fetchMock.mock.calls[1]?.[1]?.signal?.aborted).toBe(false);
    } finally {
      retireBrowserSession(replacement);
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, listener);
    }
  });

  it("refreshes a known expiring session once before concurrent GETs", async () => {
    vi.spyOn(document, "cookie", "get").mockReturnValue(`session_exp=${Date.now() - 1}`);
    const refresh = Promise.withResolvers<Response>();
    fetchMock.mockImplementation(async (input) => {
      const url = requestUrl(input);
      return url.endsWith("/auth/refresh") ? await refresh.promise : Response.json({ value: url });
    });
    const first = wikiReadHttp("/files/first", PayloadSchema);
    const second = wikiReadHttp("/files/second", PayloadSchema);
    expect(fetchMock).toHaveBeenCalledOnce();
    refresh.resolve(new Response(null));
    await expect(Promise.all([first, second])).resolves.toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("starts refresh without waiting for a stalled 401 body cleanup", async () => {
    const cleanup = Promise.withResolvers<undefined>();
    const body = new ReadableStream<Uint8Array>({ cancel: async () => await cleanup.promise });
    fetchMock
      .mockResolvedValueOnce(new Response(body, { status: 401 }))
      .mockResolvedValueOnce(new Response(null))
      .mockResolvedValueOnce(Response.json({ value: "ready" }));
    try {
      await expect(wikiReadHttp("/files/first", PayloadSchema)).resolves.toEqual({
        value: "ready",
      });
      expect(fetchMock).toHaveBeenCalledTimes(3);
    } finally {
      cleanup.resolve(undefined);
    }
  });
  it("refreshes one expired session for concurrent reads and replays each GET once", async () => {
    const refresh = Promise.withResolvers<Response>();
    const attempts = new Map<string, number>();
    fetchMock.mockImplementation(async (input) => {
      const url = requestUrl(input);
      if (url.endsWith("/auth/refresh")) return await refresh.promise;
      const attempt = (attempts.get(url) ?? 0) + 1;
      attempts.set(url, attempt);
      return attempt === 1 ? new Response(null, { status: 401 }) : Response.json({ value: url });
    });
    const first = wikiReadHttp("/files/first", PayloadSchema);
    const second = wikiReadHttp("/files/second", PayloadSchema);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    refresh.resolve(new Response(null));

    await expect(Promise.all([first, second])).resolves.toEqual([
      { value: "/api/wiki/files/first" },
      { value: "/api/wiki/files/second" },
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("keeps a temporary refresh failure from clearing the authenticated session", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));
    const expired = vi.fn<() => void>();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, expired);
    try {
      await expect(wikiReadHttp("/files/first", PayloadSchema)).rejects.toEqual(
        new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE),
      );
      expect(expired).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, expired);
    }
  });

  it("reports a terminal authentication failure without replaying a denied refresh", async () => {
    fetchMock
      .mockResolvedValueOnce(Response.json({ detail: "Your session has expired" }, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    const expired = vi.fn<() => void>();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, expired);
    try {
      await expect(wikiReadHttp("/files/first", PayloadSchema)).rejects.toMatchObject({
        status: 401,
        message: "Your session has expired",
      });
      expect(expired).toHaveBeenCalledOnce();
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, expired);
    }
  });

  it("does not dispatch an already cancelled read", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      wikiReadHttp("/files/first", PayloadSchema, {}, controller.signal),
    ).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops a cancelled read during refresh while another read can finish", async () => {
    const refresh = Promise.withResolvers<Response>();
    let isFresh = false;
    fetchMock.mockImplementation(async (input) => {
      const url = requestUrl(input);
      if (url.endsWith("/auth/refresh")) return await refresh.promise;
      return isFresh ? Response.json({ value: url }) : new Response(null, { status: 401 });
    });
    const controller = new AbortController();
    const discarded = wikiReadHttp("/files/first", PayloadSchema, {}, controller.signal);
    const discardedResult = discarded.catch((error: unknown) => error);
    const retained = wikiReadHttp("/files/second", PayloadSchema);
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    controller.abort();
    await expect(discardedResult).resolves.toMatchObject({ name: "AbortError" });
    isFresh = true;
    refresh.resolve(new Response(null));
    await expect(retained).resolves.toEqual({ value: "/api/wiki/files/second" });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("does not validate or publish an obsolete response that finishes after cancellation", async () => {
    const body = Promise.withResolvers<unknown>();
    const response = Response.json({ value: "unused" });
    const responseBody = vi.spyOn(response, "json").mockReturnValue(body.promise);
    fetchMock.mockResolvedValue(response);
    const controller = new AbortController();
    const parse = vi.fn((value: string) => value);
    const schema = z.object({ value: z.string().transform(parse) });
    const pending = wikiReadHttp("/files/first", schema, {}, controller.signal);
    const discardedResult = pending.catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(responseBody).toHaveBeenCalledOnce();
    });

    controller.abort();
    body.resolve({ value: "obsolete" });

    await expect(discardedResult).resolves.toMatchObject({ name: "AbortError" });
    expect(parse).not.toHaveBeenCalled();
  });
});

function requestUrl(input: Parameters<typeof fetch>[0]): string {
  if (typeof input === "string") return input;
  return "href" in input ? input.href : input.url;
}
