// @vitest-environment node
import { GET, POST } from "./route";

const Mocks = vi.hoisted(() => ({
  create: vi.fn(),
  read: vi.fn(),
  capabilities: vi.fn(),
  task: vi.fn(),
  cookies: vi.fn(),
}));
vi.mock("@/app/actions/smart-create-actions", () => ({
  createDocumentTransformAction: Mocks.create,
  getDocumentTransformCapabilitiesAction: Mocks.capabilities,
  getSmartCreateTaskAction: Mocks.task,
}));
vi.mock("@/lib/smart-create-server-client", () => ({ readSmartCreateApi: Mocks.read }));
vi.mock("next/headers", () => ({ cookies: Mocks.cookies }));

const KEY = "55555555-5555-4555-8555-555555555555";
const URL = "https://wiki.sarj.ai/api/document-transforms";
const headers = {
  origin: "https://wiki.sarj.ai",
  "x-wiki-transform": "1",
  "content-type": "application/json",
  "idempotency-key": KEY,
};
beforeEach(() => {
  vi.clearAllMocks();
  Mocks.create.mockResolvedValue({ ok: true, data: '{"id":"task"}' });
  Mocks.cookies.mockResolvedValue({ get: () => ({ value: "private-token" }) });
});

it("forwards exact body/KEY through existing authenticated rate-limited operation", async () => {
  const body = '{"source":{"content":"private text"}}';
  const response = await POST(new Request(URL, { method: "POST", headers, body }), {
    params: Promise.resolve({}),
  });
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(Mocks.create).toHaveBeenCalledExactlyOnceWith(body, KEY);
});

it("exposes only the typed replay bit on a successful submission", async () => {
  Mocks.create.mockResolvedValue({ ok: true, data: '{"id":"task"}', replayed: true });
  const response = await POST(new Request(URL, { method: "POST", headers, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.headers.get("idempotency-replayed")).toBe("true");
  expect(await response.text()).toBe('{"id":"task"}');
});

it("returns allowlisted reload guidance for a source revision conflict", async () => {
  Mocks.create.mockResolvedValue({
    code: "document_transform_source_changed",
    error: "private upstream details",
    ok: false,
    status: 409,
  });
  const response = await POST(new Request(URL, { method: "POST", headers, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.status).toBe(409);
  await expect(response.json()).resolves.toEqual({
    code: "document_transform_source_changed",
    error: "The document changed since polishing was requested. Reload it before trying again.",
  });
});

it("keeps unrelated conflict details and codes redacted", async () => {
  Mocks.create.mockResolvedValue({
    error: "This document already has an active transformation",
    ok: false,
    status: 409,
  });
  const response = await POST(new Request(URL, { method: "POST", headers, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.status).toBe(409);
  await expect(response.json()).resolves.toEqual({
    error: "Document transform request could not be completed",
  });
});

it("does not expose a source-change code on a non-conflict response", async () => {
  Mocks.create.mockResolvedValue({
    code: "document_transform_source_changed",
    error: "private upstream details",
    ok: false,
    status: 503,
  });
  const response = await POST(new Request(URL, { method: "POST", headers, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.status).toBe(503);
  await expect(response.json()).resolves.toEqual({
    error: "Document transform request could not be completed",
  });
});

it.each([
  { origin: "https://attacker.example" },
  { origin: "null" },
  { "x-wiki-transform": "" },
  { "sec-fetch-site": "cross-site" },
  { "content-type": "text/plain" },
  { "idempotency-key": "../secret" },
])("rejects unsafe request headers %j before admission", async (override) => {
  const response = await POST(
    new Request(URL, { method: "POST", headers: { ...headers, ...override }, body: "{}" }),
    { params: Promise.resolve({}) },
  );
  expect(response.status).toBeGreaterThanOrEqual(400);
  expect(Mocks.create).not.toHaveBeenCalled();
});

it("never proxies an arbitrary path", async () => {
  const response = await GET(new Request(URL, { headers }), {
    params: Promise.resolve({ path: ["secrets", KEY] }),
  });
  expect(response.status).toBe(404);
  expect(Mocks.read).not.toHaveBeenCalled();
});

it("looks up only a validated KEY with server-side authentication", async () => {
  Mocks.read.mockResolvedValue({
    ok: true,
    body: JSON.stringify({ task_id: KEY, status: "pending" }),
  });
  const response = await GET(new Request(URL, { headers }), {
    params: Promise.resolve({ path: ["admissions", KEY] }),
  });
  expect(response.status).toBe(200);
  expect(Mocks.read).toHaveBeenCalledExactlyOnceWith(
    `/document-transforms/admissions/${KEY}`,
    "private-token",
  );
});

it("redacts upstream exception details", async () => {
  Mocks.create.mockRejectedValue(new Error("private draft or token"));
  const response = await POST(new Request(URL, { method: "POST", headers, body: "{}" }), {
    params: Promise.resolve({}),
  });
  expect(response.status).toBe(502);
  expect(await response.text()).not.toContain("private draft");
});

it("rejects missing Origin without trusting a forged content length", async () => {
  const missingOrigin = new Headers(headers);
  missingOrigin.delete("origin");
  const response = await POST(
    new Request(URL, { method: "POST", headers: missingOrigin, body: "{}" }),
    { params: Promise.resolve({}) },
  );
  expect(response.status).toBe(403);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(Mocks.create).not.toHaveBeenCalled();
});

it("enforces actual streamed bytes regardless of Content-Length", async () => {
  const response = await POST(
    new Request(URL, {
      method: "POST",
      headers: { ...headers, "content-length": "1" },
      body: "x".repeat(4 * 1024 * 1024 + 1),
    }),
    { params: Promise.resolve({}) },
  );
  expect(response.status).toBe(413);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(Mocks.create).not.toHaveBeenCalled();
});

it.each([{}, { "x-wiki-transform": "1", "sec-fetch-site": "cross-site" }])(
  "rejects cross-origin reads %j without CORS",
  async (requestHeaders) => {
    const response = await GET(new Request(URL, { headers: requestHeaders }), {
      params: Promise.resolve({ path: ["admissions", KEY] }),
    });
    expect(response.status).toBe(403);
    expect(response.headers.get("access-control-allow-origin")).toBeNull();
    expect(Mocks.read).not.toHaveBeenCalled();
  },
);

it("does not forward admissions for an unauthenticated caller", async () => {
  Mocks.cookies.mockResolvedValue({ get: vi.fn() });
  const response = await GET(new Request(URL, { headers }), {
    params: Promise.resolve({ path: ["admissions", KEY] }),
  });
  expect(response.status).toBe(401);
  expect(Mocks.read).not.toHaveBeenCalled();
});
