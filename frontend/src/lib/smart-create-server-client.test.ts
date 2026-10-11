// @vitest-environment node

import { smartCreateServerClient } from "@/lib/smart-create-server-client";

describe("smartCreateServerClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
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
