// @vitest-environment node

import { smartCreateServerClient } from "@/lib/smart-create-server-client";

function timeoutSignal(milliseconds: number): AbortSignal {
  const controller = new AbortController();
  setTimeout(
    () => controller.abort(new DOMException("Deadline exceeded", "TimeoutError")),
    milliseconds,
  );
  return controller.signal;
}

describe("smartCreateServerClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("stops an oversized failure body at the error bound without retry or private guidance", async () => {
    let received = 0;
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          received += 8192;
          controller.enqueue(new Uint8Array(8192).fill(120));
        },
        cancel,
      },
      { highWaterMark: 0 },
    );
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(smartCreateServerClient("/files/smart", "{}", "token", "key")).resolves.toEqual({
      body: "",
      error: "Smart Create could not reach the API",
      ok: false,
      status: 502,
    });
    expect(received).toBe(40_960);
    expect(cancel).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("keeps the larger success-body contract for admitted mutations", async () => {
    const body = JSON.stringify({ result: "x".repeat(64 * 1024) });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 202 })),
    );
    await expect(smartCreateServerClient("/files/smart", "{}", "token", "key")).resolves.toEqual({
      body,
      ok: true,
      status: 202,
    });
  });

  it("keeps the thirty-second POST deadline through stalled response ingestion without retry", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation(timeoutSignal);
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{"));
      },
      cancel,
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const pending = smartCreateServerClient(
      "/document-transforms",
      "{}",
      "fixture-token",
      "same-key",
    );
    try {
      await vi.advanceTimersByTimeAsync(29_999);
      expect(body.locked).toBe(true);
      expect(cancel).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toEqual({
        body: "",
        error: "Smart Create could not reach the API",
        ok: false,
        status: 502,
      });
      expect(cancel).toHaveBeenCalledOnce();
      expect(fetchMock).toHaveBeenCalledOnce();
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
      expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("idempotency-key")).toBe(
        "same-key",
      );
    } finally {
      cleanup.resolve(undefined);
    }
  });

  it("preserves a mutation body arriving inside the original total deadline", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation(timeoutSignal);
    let stream: ReadableStreamDefaultController<Uint8Array> | undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        stream = controller;
      },
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const pending = smartCreateServerClient(
      "/document-transforms",
      "{}",
      "fixture-token",
      "same-key",
    );
    await vi.advanceTimersByTimeAsync(25_000);
    stream?.enqueue(new TextEncoder().encode('{"id":"task"}'));
    stream?.close();
    await expect(pending).resolves.toEqual({ body: '{"id":"task"}', ok: true, status: 202 });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("rejects a backend redirect without replaying an authenticated mutation", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("private redirect", {
        headers: { location: "https://other-origin.test/private" },
        status: 307,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      smartCreateServerClient("/document-transforms", "{}", "token", "key"),
    ).resolves.toEqual({ body: "", ok: false, status: 502 });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[1]?.redirect).toBe("manual");
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("idempotency-key")).toBe("key");
  });

  it.each(["true", "false"])("captures an exact idempotency replay header: %s", async (value) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response('{"id":"task"}', {
          headers: { "Idempotency-Replayed": value },
          status: 202,
        }),
      ),
    );

    await expect(
      smartCreateServerClient("/document-transforms", "{}", "token", "key"),
    ).resolves.toEqual({
      body: '{"id":"task"}',
      idempotencyReplayed: value === "true",
      ok: true,
      status: 202,
    });
  });

  it("ignores an invalid replay header", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("{}", {
          headers: { "Idempotency-Replayed": "yes" },
          status: 202,
        }),
      ),
    );

    await expect(
      smartCreateServerClient("/document-transforms", "{}", "token", "key"),
    ).resolves.toEqual({
      body: "{}",
      ok: true,
      status: 202,
    });
  });

  it("preserves only the allowlisted source-change error code", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          Response.json(
            { detail: "safe" },
            {
              headers: { "X-Wiki-Error-Code": "document_transform_source_changed" },
              status: 409,
            },
          ),
        )
        .mockResolvedValueOnce(
          Response.json(
            { detail: "private" },
            { headers: { "X-Wiki-Error-Code": "task_idempotency_conflict" }, status: 409 },
          ),
        ),
    );

    await expect(
      smartCreateServerClient("/document-transforms", "{}", "token", "key"),
    ).resolves.toEqual({
      body: '{"detail":"safe"}',
      error: "safe",
      errorCode: "document_transform_source_changed",
      ok: false,
      status: 409,
    });
    await expect(
      smartCreateServerClient("/document-transforms", "{}", "token", "key"),
    ).resolves.toEqual({
      body: '{"detail":"private"}',
      error: "private",
      ok: false,
      status: 409,
    });
  });

  it("ignores a source-change header on a non-conflict response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { detail: "upstream unavailable" },
          {
            headers: { "X-Wiki-Error-Code": "document_transform_source_changed" },
            status: 503,
          },
        ),
      ),
    );

    await expect(
      smartCreateServerClient("/document-transforms", "{}", "token", "key"),
    ).resolves.toEqual({
      body: '{"detail":"upstream unavailable"}',
      error: "upstream unavailable",
      ok: false,
      status: 503,
    });
  });
});
