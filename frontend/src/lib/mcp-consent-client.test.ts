import {
  decideMcpConsent,
  disconnectMcpConnection,
  getMcpConnections,
  getMcpConsent,
  McpConsentRequestError,
  recoverMcpConsent,
} from "./mcp-consent-client";

const INTENT = `wm_intent_${"a".repeat(43)}`;
const CSRF = `wm_intent_${"b".repeat(43)}`;
const FIELDS = {
  accountEmail: "account_email",
  clientId: "client_id",
  clientName: "client_name",
  expiresAt: "expires_at",
  grantId: "grant_id",
  redirectUri: "redirect_uri",
} as const;

describe("MCP consent transport", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("stops oversized successful JSON before consuming its unused fields", async () => {
    const payload = new TextEncoder().encode(
      JSON.stringify({
        [FIELDS.accountEmail]: "person@example.com",
        [FIELDS.clientId]: "app",
        [FIELDS.clientName]: "App",
        csrf: CSRF,
        scopes: ["wiki:read"],
        status: "pending",
        ignored: "x".repeat(1024 * 1024),
      }),
    );
    let delivered = 0;
    const cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          if (delivered === payload.byteLength) {
            controller.close();
            return;
          }
          const end = Math.min(delivered + 8192, payload.byteLength);
          controller.enqueue(payload.slice(delivered, end));
          delivered = end;
        },
        cancel: cancelled,
      },
      { highWaterMark: 0 },
    );
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getMcpConsent(INTENT)).rejects.toMatchObject({
      name: "McpConsentRequestError",
      status: 502,
    });
    expect(delivered).toBe(256 * 1024 + 8192);
    expect(cancelled).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each([
    ["maximum registered IDs", "a", 41_691],
    ["conservative JSON-escape stress", "\u{0000}", 169_191],
  ] as const)(
    "validates a 100-connection inventory with %s",
    async (_kind, character, expectedBytes) => {
      const connections = {
        csrf: "a".repeat(64),
        connections: Array.from({ length: 100 }, (_, index) => ({
          capabilities: ["wiki:read", "wiki:transform"],
          [FIELDS.clientId]: character.repeat(255),
          [FIELDS.expiresAt]: "2026-10-11T12:00:00.999999+00:00",
          [FIELDS.grantId]: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
        })),
      };
      const bytes = new TextEncoder().encode(JSON.stringify(connections)).byteLength;
      expect(bytes).toBe(expectedBytes);
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(connections)));

      await expect(getMcpConnections()).resolves.toEqual(connections);
    },
  );

  it("retains maximum consent fields and a long registered callback with bounded OAuth parameters", async () => {
    const view = {
      [FIELDS.accountEmail]: "person@example.com",
      [FIELDS.clientId]: "a".repeat(255),
      [FIELDS.clientName]: "b".repeat(256),
      csrf: CSRF,
      scopes: ["wiki:read", "wiki:transform"],
      status: "pending",
    };
    const prefix = "http://127.0.0.1:49152/";
    const registeredCallback = `${prefix}${"a".repeat(2048 - prefix.length)}`;
    const redirect = `${registeredCallback}?state=${"x".repeat(2048)}&code=synthetic&iss=https%3A%2F%2Fwiki.example`;
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(view))
      .mockResolvedValueOnce(Response.json({ [FIELDS.redirectUri]: redirect }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getMcpConsent(INTENT)).resolves.toEqual(view);
    await expect(
      decideMcpConsent(INTENT, CSRF, { approve: true, transformApproved: true }),
    ).resolves.toBe(redirect);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([0, 1])("enforces the exact response byte bound with %s extra bytes", async (extra) => {
    const payload = {
      [FIELDS.accountEmail]: "person@example.com",
      [FIELDS.clientId]: "app",
      [FIELDS.clientName]: "App",
      csrf: CSRF,
      scopes: ["wiki:read"],
      status: "pending",
      ignored: "",
    };
    const prefixBytes = new TextEncoder().encode(JSON.stringify(payload)).byteLength;
    const maximum = 256 * 1024;
    payload.ignored = "x".repeat(maximum - prefixBytes + extra);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(Response.json(payload));
    vi.stubGlobal("fetch", fetchMock);

    const result: unknown = await getMcpConsent(INTENT).catch((error: unknown) => error);
    const expected = extra === 0 ? { [FIELDS.clientName]: "App" } : { status: 502 };
    expect(result).toMatchObject(expected);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each(["consent", "recovery", "connections"] as const)(
    "does not dispatch an already-abandoned %s read",
    async (kind) => {
      const controller = new AbortController();
      controller.abort();
      const fetchMock = vi.fn<typeof fetch>();
      vi.stubGlobal("fetch", fetchMock);
      const pending =
        kind === "consent"
          ? getMcpConsent(INTENT, controller.signal)
          : kind === "recovery"
            ? recoverMcpConsent(INTENT, controller.signal)
            : getMcpConnections(controller.signal);

      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("discards late read headers after cancellation before any body work", async () => {
    const controller = new AbortController();
    const response = Promise.withResolvers<Response>();
    const cancelled = vi.fn();
    const pulled = vi.fn();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", fetchMock);
    const pending = getMcpConsent(INTENT, controller.signal);
    controller.abort();
    response.resolve(
      new Response(
        new ReadableStream<Uint8Array>({ cancel: cancelled, pull: pulled }, { highWaterMark: 0 }),
      ),
    );

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toHaveBeenCalledOnce();
    expect(pulled).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("preserves an admitted consent decision when its earlier read owner is retired", async () => {
    const controller = new AbortController();
    const decision = Promise.withResolvers<Response>();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json({
          [FIELDS.accountEmail]: "person@example.com",
          [FIELDS.clientId]: "app",
          [FIELDS.clientName]: "App",
          csrf: CSRF,
          scopes: ["wiki:read"],
          status: "pending",
        }),
      )
      .mockReturnValueOnce(decision.promise);
    vi.stubGlobal("fetch", fetchMock);
    await getMcpConsent(INTENT, controller.signal);
    const pending = decideMcpConsent(INTENT, CSRF, { approve: true, transformApproved: false });

    controller.abort();

    expect(fetchMock.mock.calls[1]?.[1]?.signal?.aborted).toBe(false);
    const redirect = "http://127.0.0.1:49152/callback?code=synthetic";
    decision.resolve(Response.json({ [FIELDS.redirectUri]: redirect }));
    await expect(pending).resolves.toBe(redirect);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["caller", "deadline"] as const)(
    "cancels unfinished successful decoding when its %s stops waiting",
    async (reason) => {
      const caller = new AbortController();
      const deadline = new AbortController();
      vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
      const cleanup = Promise.withResolvers<undefined>();
      const cancelled = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode("{"));
        },
        cancel: cancelled,
      });
      vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(body)));
      const pending = getMcpConsent(INTENT, caller.signal);
      await vi.waitFor(() => expect(body.locked).toBe(true));
      if (reason === "caller") caller.abort();
      else deadline.abort(new DOMException("The operation timed out", "TimeoutError"));
      try {
        await expect(pending).rejects.toMatchObject({
          name: reason === "caller" ? "AbortError" : "TimeoutError",
        });
        expect(cancelled).toHaveBeenCalledOnce();
      } finally {
        cleanup.resolve(undefined);
      }
    },
  );

  it.each([new Uint8Array([0xff]), new TextEncoder().encode("invalid JSON")])(
    "redacts a malformed successful body",
    async (bytes) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(bytes)));
      await expect(getMcpConnections()).rejects.toMatchObject({
        message: "Wiki connection request unavailable",
        status: 502,
      });
    },
  );

  it.each(["read", "decision", "disconnect"] as const)(
    "releases unused %s error bodies without waiting for cleanup or retrying",
    async (operation) => {
      const cleanup = Promise.withResolvers<undefined>();
      const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
      const response = new Response(new ReadableStream<Uint8Array>({ cancel }), { status: 503 });
      const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response);
      vi.stubGlobal("fetch", fetchMock);
      try {
        const pending =
          operation === "read"
            ? getMcpConsent(INTENT)
            : operation === "decision"
              ? decideMcpConsent(INTENT, CSRF, { approve: true, transformApproved: false })
              : disconnectMcpConnection("00000000-0000-4000-8000-000000000001", "a".repeat(64));
        await expect(pending).rejects.toMatchObject({
          name: "McpConsentRequestError",
          status: 503,
        });
        expect(cancel).toHaveBeenCalledOnce();
        expect(fetchMock).toHaveBeenCalledOnce();
      } finally {
        cleanup.resolve(undefined);
      }
    },
  );

  it("disconnects with browser credentials and CSRF, accepting only a confirmed 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      disconnectMcpConnection("00000000-0000-4000-8000-000000000001", "a".repeat(64)),
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining("/connections/00000000-0000-4000-8000-000000000001"),
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        headers: { "X-MCP-CSRF": "a".repeat(64) },
        redirect: "error",
      }),
    );
  });

  it.each([200, 401, 503])("does not confirm or replay a disconnect on HTTP %s", async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      disconnectMcpConnection("00000000-0000-4000-8000-000000000001", "a".repeat(64)),
    ).rejects.toThrow(McpConsentRequestError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("uses browser credentials, no cache, no referrer, and no redirected reads", async () => {
    const view = {
      account_email: "person@example.com",
      client_id: "app",
      client_name: "App",
      csrf: CSRF,
      scopes: ["wiki:read"],
      status: "pending",
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(view));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getMcpConsent(INTENT)).resolves.toEqual(view);
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining(`/consent/${INTENT}`),
      expect.objectContaining({
        cache: "no-store",
        credentials: "include",
        redirect: "error",
        referrerPolicy: "no-referrer",
      }),
    );
  });

  it("does not send malformed intents or CSRF values", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(getMcpConsent("../../auth")).rejects.toThrow();
    await expect(
      decideMcpConsent(INTENT, "bad", { approve: true, transformApproved: false }),
    ).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([401, 500])("does not replay a decision after HTTP %s", async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("private error", { status }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      decideMcpConsent(INTENT, CSRF, { approve: true, transformApproved: false }),
    ).rejects.toThrow(McpConsentRequestError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("does not replay a decision after transport failure", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      decideMcpConsent(INTENT, CSRF, { approve: true, transformApproved: false }),
    ).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("sends the explicit decision and accepts the server-validated loopback callback", async () => {
    const redirect =
      "http://127.0.0.1:49152/callback?code=synthetic&iss=https%3A%2F%2Fwiki.example";
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ redirect_uri: redirect }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      decideMcpConsent(INTENT, CSRF, { approve: false, transformApproved: true }),
    ).resolves.toBe(redirect);
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.any(String),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approve: false, csrf: CSRF, transform_approved: false }),
      }),
    );
  });

  it.each([
    "javascript:alert(1)",
    "https://attacker.example/callback".replace("https:", "http:"),
    "https://user:pass@attacker.example/callback",
    "http://127.0.0.1/callback",
    "https://app.example/callback#token",
  ])("rejects unsafe callback %s", async (redirect) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ redirect_uri: redirect })));
    await expect(
      decideMcpConsent(INTENT, CSRF, { approve: true, transformApproved: false }),
    ).rejects.toThrow();
  });

  it("reads terminal status without submitting a decision", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json({ status: "approved", restart_required: true }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(recoverMcpConsent(INTENT)).resolves.toEqual({
      status: "approved",
      restart_required: true,
    });
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining(`/consent/${INTENT}/status`),
      expect.not.objectContaining({ method: "POST" }),
    );
  });
});
