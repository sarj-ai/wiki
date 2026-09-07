// @vitest-environment node

import { callSmartCreateApi } from "@/lib/smart-create-server-client";

describe("callSmartCreateApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
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

    await expect(callSmartCreateApi("/document-transforms", "{}", "token", "key")).resolves.toEqual(
      {
        body: '{"id":"task"}',
        idempotencyReplayed: value === "true",
        ok: true,
        status: 202,
      },
    );
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

    await expect(callSmartCreateApi("/document-transforms", "{}", "token", "key")).resolves.toEqual(
      {
        body: "{}",
        ok: true,
        status: 202,
      },
    );
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

    await expect(callSmartCreateApi("/document-transforms", "{}", "token", "key")).resolves.toEqual(
      {
        body: '{"detail":"safe"}',
        error: "safe",
        errorCode: "document_transform_source_changed",
        ok: false,
        status: 409,
      },
    );
    await expect(callSmartCreateApi("/document-transforms", "{}", "token", "key")).resolves.toEqual(
      {
        body: '{"detail":"private"}',
        error: "private",
        ok: false,
        status: 409,
      },
    );
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

    await expect(callSmartCreateApi("/document-transforms", "{}", "token", "key")).resolves.toEqual(
      {
        body: '{"detail":"upstream unavailable"}',
        error: "upstream unavailable",
        ok: false,
        status: 503,
      },
    );
  });
});
