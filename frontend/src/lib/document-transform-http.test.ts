// @vitest-environment happy-dom

import { transformHttp } from "@/lib/document-transform-http";

describe("transformHttp", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
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
});
