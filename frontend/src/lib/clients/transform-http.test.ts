// @vitest-environment happy-dom

import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";
import { transformHttp } from "@/lib/clients/transform-http";

describe("transformHttp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("cancels unsignalled transform reads on session retirement", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const response = Promise.withResolvers<Response>();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const pending = transformHttp("/tasks/id").catch((error: unknown) => error);
    try {
      retireBrowserSession(owner);
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    } finally {
      response.resolve(new Response(null));
      await pending;
      retireBrowserSession(owner);
    }
  });

  it("retires an unfinished read body without a caller signal", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{"));
      },
      cancel,
    });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(body)));
    const pending = transformHttp("/tasks/id").catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(body.locked).toBe(true);
    });
    retireBrowserSession(owner);
    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("lets an admitted POST finish before discarding its retired response", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const response = Promise.withResolvers<Response>();
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const pending = transformHttp("", "{}", crypto.randomUUID()).catch((error: unknown) => error);
    try {
      retireBrowserSession(owner);
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
      response.resolve(new Response(new ReadableStream<Uint8Array>({ cancel }), { status: 202 }));
      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
      expect(cancel).toHaveBeenCalledOnce();
      expect(fetchMock).toHaveBeenCalledOnce();
    } finally {
      cleanup.resolve(undefined);
      retireBrowserSession(owner);
    }
  });

  it("does not decode an oversized transform failure as user guidance", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(32 * 1024 + 1));
      },
      cancel,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 502 })),
    );
    await expect(transformHttp("/tasks/id")).resolves.toMatchObject({
      error: "Document transform request could not be completed",
      ok: false,
      status: 502,
    });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("does not dispatch a transform read after cancellation", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(
      transformHttp("/tasks/id", undefined, undefined, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("cancels decoding an unfinished transform read body", async () => {
    const controller = new AbortController();
    const cancelBody = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(stream) {
        stream.enqueue(new TextEncoder().encode("{"));
      },
      cancel: cancelBody,
    });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(body)));
    const pending = transformHttp("/tasks/id", undefined, undefined, controller.signal);
    const result = pending.catch((error: unknown) => error);
    await vi.waitFor(() => {
      expect(body.locked).toBe(true);
    });

    controller.abort();

    await expect(result).resolves.toMatchObject({ name: "AbortError" });
    expect(cancelBody).toHaveBeenCalledOnce();
  });

  it.each(["true", "false"])("preserves a valid replay header on POST: %s", async (value) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response('{"id":"task"}', {
          headers: { "Idempotency-Replayed": value },
          status: 202,
        }),
      ),
    );

    await expect(transformHttp("", "{}", crypto.randomUUID())).resolves.toEqual({
      data: '{"id":"task"}',
      ok: true,
      replayed: value === "true",
    });
  });

  it("preserves same-origin conflict guidance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            code: "document_transform_source_changed",
            error:
              "The document changed since polishing was requested. Reload it before trying again.",
          },
          { status: 409 },
        ),
      ),
    );

    await expect(transformHttp("", "{}", crypto.randomUUID())).resolves.toEqual({
      code: "document_transform_source_changed",
      error: "The document changed since polishing was requested. Reload it before trying again.",
      ok: false,
      status: 409,
    });
  });

  it("ignores a non-allowlisted error code", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ code: "private_code", error: "Generic" }, { status: 409 }),
        ),
    );

    await expect(transformHttp("", "{}", crypto.randomUUID())).resolves.toEqual({
      error: "Generic",
      ok: false,
      status: 409,
    });
  });

  it("does not invent replay metadata when the BFF omits the header", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response('{"id":"task"}', { status: 202 })),
    );

    await expect(transformHttp("", "{}", crypto.randomUUID())).resolves.toEqual({
      data: '{"id":"task"}',
      ok: true,
    });
  });

  it("redacts malformed failure responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("private failure", { status: 502 })),
    );

    await expect(transformHttp("/tasks/id")).resolves.toEqual({
      error: "Document transform request could not be completed",
      ok: false,
      status: 502,
    });
  });

  it.each([
    {
      name: "null payload",
      payload: null,
      expected: { error: "Document transform request could not be completed" },
    },
    {
      name: "array payload",
      payload: [],
      expected: { error: "Document transform request could not be completed" },
    },
    {
      name: "invalid text with a valid code",
      payload: { error: 42, code: "document_transform_source_changed" },
      expected: {
        code: "document_transform_source_changed",
        error: "Document transform request could not be completed",
      },
    },
    {
      name: "empty text with an invalid code",
      payload: { error: "", code: 42 },
      expected: { error: "" },
    },
    {
      name: "valid text with a null code",
      payload: { error: "Safe guidance", code: null },
      expected: { error: "Safe guidance" },
    },
  ])("validates independent error fields: $name", async ({ payload, expected }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(Response.json(payload, { status: 409 })),
    );

    await expect(transformHttp("/tasks/id")).resolves.toEqual({
      ...expected,
      ok: false,
      status: 409,
    });
  });
});
