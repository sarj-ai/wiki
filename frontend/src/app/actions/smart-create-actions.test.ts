import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  callApi: vi.fn(),
  cookies: vi.fn(),
  createLimit: vi.fn(),
  createBindingAvailable: true,
  headers: vi.fn(),
  proposalLimit: vi.fn(),
}));

vi.mock("next/headers", () => ({
  cookies: mocks.cookies,
  headers: mocks.headers,
}));

vi.mock("@opennextjs/cloudflare", () => ({
  getCloudflareContext: () => ({
    env: {
      ...(mocks.createBindingAvailable && {
        SMART_CREATE_RATE_LIMITER: { limit: mocks.createLimit },
      }),
      SMART_PROPOSAL_RATE_LIMITER: { limit: mocks.proposalLimit },
    },
  }),
}));

vi.mock("@/lib/smart-create-server-client", () => ({
  smartCreateServerClient: mocks.callApi,
}));

import {
  createDocumentTransformAction,
  createSmartFileAction,
  proposeFileReorganizationAction,
  proposeSmartFileAction,
} from "./smart-create-actions";

describe("Smart Create edge actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createBindingAvailable = true;
    mocks.cookies.mockResolvedValue({ get: () => ({ value: "access-token" }) });
    mocks.headers.mockResolvedValue(new Headers({ "cf-connecting-ip": "203.0.113.10" }));
    mocks.createLimit.mockResolvedValue({ success: true });
    mocks.proposalLimit.mockResolvedValue({ success: true });
    mocks.callApi.mockResolvedValue({ body: "{}", ok: true, status: 200 });
  });

  it.each(["binding", "address"])(
    "fails closed in production without trusted limiter %s",
    async (missing) => {
      vi.stubEnv("NODE_ENV", "production");
      mocks.createBindingAvailable = missing !== "binding";
      if (missing === "address") mocks.headers.mockResolvedValue(new Headers());
      try {
        await expect(
          createDocumentTransformAction("{}", "55555555-5555-4555-8555-555555555555"),
        ).resolves.toMatchObject({ ok: false, status: 503 });
        expect(mocks.callApi).not.toHaveBeenCalled();
      } finally {
        vi.unstubAllEnvs();
      }
    },
  );

  it("limits proposals by the Cloudflare client address", async () => {
    await expect(proposeSmartFileAction('{"kind":"ai"}')).resolves.toEqual({
      data: "{}",
      ok: true,
    });

    expect(mocks.proposalLimit).toHaveBeenCalledWith({ key: "203.0.113.10" });
    expect(mocks.callApi).toHaveBeenCalledWith(
      "/files/smart-proposal",
      '{"kind":"ai"}',
      "access-token",
      undefined,
    );
  });

  it("rejects an over-limit request before calling the backend", async () => {
    mocks.proposalLimit.mockResolvedValue({ success: false });

    await expect(proposeSmartFileAction("{}")).resolves.toEqual({
      error: "Too many requests. Try again in a minute.",
      ok: false,
      status: 429,
    });
    expect(mocks.callApi).not.toHaveBeenCalled();
  });

  it("normalizes an upstream creation rate limit", async () => {
    mocks.callApi.mockResolvedValue({ body: "", ok: false, status: 429 });

    await expect(proposeSmartFileAction("{}")).resolves.toEqual({
      error: "Too many requests. Try again in a minute.",
      ok: false,
      status: 429,
    });
  });

  it("uses the proposal limit for document reorganization", async () => {
    await expect(proposeFileReorganizationAction("file/with spaces")).resolves.toEqual({
      data: "{}",
      ok: true,
    });

    expect(mocks.proposalLimit).toHaveBeenCalledWith({ key: "203.0.113.10" });
    expect(mocks.callApi).toHaveBeenCalledWith(
      "/files/file%2Fwith%20spaces/reorganize-proposal",
      "{}",
      "access-token",
      undefined,
    );
  });

  it("forwards creation idempotency keys after the independent create limit", async () => {
    await createSmartFileAction("{}", "55555555-5555-4555-8555-555555555555");

    expect(mocks.createLimit).toHaveBeenCalledWith({ key: "203.0.113.10" });
    expect(mocks.callApi).toHaveBeenCalledWith(
      "/files/smart",
      "{}",
      "access-token",
      "55555555-5555-4555-8555-555555555555",
    );
  });

  it("routes document transforms through the creation limit with idempotency", async () => {
    await createDocumentTransformAction('{"mode":"both"}', "66666666-6666-4666-8666-666666666666");

    expect(mocks.createLimit).toHaveBeenCalledWith({ key: "203.0.113.10" });
    expect(mocks.callApi).toHaveBeenCalledWith(
      "/document-transforms",
      '{"mode":"both"}',
      "access-token",
      "66666666-6666-4666-8666-666666666666",
    );
  });

  it("preserves document-transform replay metadata", async () => {
    mocks.callApi.mockResolvedValue({
      body: '{"id":"task"}',
      idempotencyReplayed: true,
      ok: true,
      status: 202,
    });

    await expect(
      createDocumentTransformAction("{}", "66666666-6666-4666-8666-666666666666"),
    ).resolves.toEqual({ data: '{"id":"task"}', ok: true, replayed: true });
  });

  it("preserves only the typed document-transform source conflict", async () => {
    mocks.callApi.mockResolvedValue({
      body: "{}",
      error: "safe detail",
      errorCode: "document_transform_source_changed",
      ok: false,
      status: 409,
    });

    await expect(
      createDocumentTransformAction("{}", "66666666-6666-4666-8666-666666666666"),
    ).resolves.toEqual({
      code: "document_transform_source_changed",
      error: "safe detail",
      ok: false,
      status: 409,
    });
  });
});
