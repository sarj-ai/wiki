import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  callApi: vi.fn(),
  cookies: vi.fn(),
  createLimit: vi.fn(),
  headers: vi.fn(),
  proposalLimit: vi.fn(),
  readApi: vi.fn(),
}));

vi.mock("next/headers", () => ({
  cookies: mocks.cookies,
  headers: mocks.headers,
}));

vi.mock("@opennextjs/cloudflare", () => ({
  getCloudflareContext: () => ({
    env: {
      SMART_CREATE_RATE_LIMITER: { limit: mocks.createLimit },
      SMART_PROPOSAL_RATE_LIMITER: { limit: mocks.proposalLimit },
    },
  }),
}));

vi.mock("@/lib/smart-create-server-client", () => ({
  callSmartCreateApi: mocks.callApi,
  readSmartCreateApi: mocks.readApi,
}));

import {
  createSmartFileAction,
  getSmartCreateTaskAction,
  listSmartTemplatesAction,
  proposeFileReorganizationAction,
  proposeSmartFileAction,
} from "./smart-create-actions";

describe("Smart Create edge actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.cookies.mockResolvedValue({ get: () => ({ value: "access-token" }) });
    mocks.headers.mockResolvedValue(new Headers({ "cf-connecting-ip": "203.0.113.10" }));
    mocks.createLimit.mockResolvedValue({ success: true });
    mocks.proposalLimit.mockResolvedValue({ success: true });
    mocks.callApi.mockResolvedValue({ body: "{}", ok: true, status: 200 });
    mocks.readApi.mockResolvedValue({ body: "{}", ok: true, status: 200 });
  });

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

  it("polls tasks and lists templates through authenticated server reads", async () => {
    await expect(getSmartCreateTaskAction("55555555-5555-4555-8555-555555555555")).resolves.toEqual(
      { data: "{}", ok: true },
    );
    await expect(listSmartTemplatesAction(true)).resolves.toEqual({ data: "{}", ok: true });

    expect(mocks.readApi).toHaveBeenNthCalledWith(
      1,
      "/tasks/55555555-5555-4555-8555-555555555555",
      "access-token",
    );
    expect(mocks.readApi).toHaveBeenNthCalledWith(2, "/templates?refresh=true", "access-token");
  });
});
