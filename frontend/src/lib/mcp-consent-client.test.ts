import {
  decideMcpConsent,
  disconnectMcpConnection,
  getMcpConsent,
  McpConsentRequestError,
  recoverMcpConsent,
} from "./mcp-consent-client";

const INTENT = `wm_intent_${"a".repeat(43)}`;
const CSRF = `wm_intent_${"b".repeat(43)}`;

describe("MCP consent transport", () => {
  afterEach(() => vi.unstubAllGlobals());

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
