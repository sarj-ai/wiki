// @vitest-environment node
import { GET, POST } from "./route";

const MOCKS = vi.hoisted(() => ({
  create: vi.fn(),
  upload: vi.fn(),
  cookies: vi.fn(),
}));
vi.mock("@/app/actions/smart-create-actions", () => ({
  createDocumentTransformAction: MOCKS.create,
  createDocumentTransformUploadAction: MOCKS.upload,
}));
vi.mock("next/headers", () => ({ cookies: MOCKS.cookies }));

const FETCH_MOCK = vi.fn<typeof fetch>();
const KEY = "55555555-5555-4555-8555-555555555555";
const URL = "https://wiki.sarj.ai/api/document-transforms";
const HEADERS = {
  origin: "https://wiki.sarj.ai",
  "x-wiki-transform": "1",
  "content-type": "application/json",
  "idempotency-key": KEY,
};
beforeEach(() => {
  vi.clearAllMocks();
  MOCKS.create.mockResolvedValue({ ok: true, data: '{"id":"task"}' });
  MOCKS.upload.mockResolvedValue({ ok: true, data: '{"upload_id":"signed"}' });
  FETCH_MOCK.mockReset();
  FETCH_MOCK.mockResolvedValue(Response.json({ result: null }));
  vi.stubGlobal("fetch", FETCH_MOCK);
  MOCKS.cookies.mockResolvedValue({ get: () => ({ value: "private-token" }) });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("skips create admission when the request is already aborted", async () => {
  const controller = new AbortController();
  controller.abort();
  const response = await POST(
    new Request(URL, {
      method: "POST",
      headers: HEADERS,
      body: "{}",
      signal: controller.signal,
    }),
    { params: Promise.resolve({}) },
  );

  expect(response.status).toBe(502);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(MOCKS.create).not.toHaveBeenCalled();
  expect(MOCKS.upload).not.toHaveBeenCalled();
});

it("skips upload admission when the request aborts while awaiting params", async () => {
  const controller = new AbortController();
  const params = Promise.withResolvers<{ path: string[] }>();
  const pending = POST(
    new Request(`${URL}/uploads`, {
      method: "POST",
      headers: HEADERS,
      body: "{}",
      signal: controller.signal,
    }),
    { params: params.promise },
  );

  controller.abort();
  params.resolve({ path: ["uploads"] });
  const response = await pending;
  expect(response.status).toBe(502);
  expect(MOCKS.upload).not.toHaveBeenCalled();
  expect(MOCKS.create).not.toHaveBeenCalled();
});

it("cancels unfinished request ingress before create admission", async () => {
  const controller = new AbortController();
  const cancel = vi.fn();
  const pull = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(stream) {
      stream.enqueue(new TextEncoder().encode("{"));
    },
    pull,
    cancel,
  });
  const options = {
    method: "POST",
    headers: HEADERS,
    body,
    duplex: "half",
    signal: controller.signal,
  };
  const pending = POST(new Request(URL, options), { params: Promise.resolve({}) });
  await vi.waitFor(() => {
    expect(pull).toHaveBeenCalled();
  });

  controller.abort();
  const response = await pending;
  expect(response.status).toBe(502);
  expect(cancel).toHaveBeenCalledOnce();
  expect(MOCKS.create).not.toHaveBeenCalled();
});

it("bounds unfinished request ingress with the existing 30-second deadline", async () => {
  const deadline = new AbortController();
  const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(stream) {
      stream.enqueue(new TextEncoder().encode("{"));
    },
    cancel,
  });
  const options = { method: "POST", headers: HEADERS, body, duplex: "half" };
  try {
    const pending = POST(new Request(URL, options), { params: Promise.resolve({}) });
    await vi.waitFor(() => {
      expect(timeout).toHaveBeenCalledExactlyOnceWith(30_000);
    });

    deadline.abort(new DOMException("Ingress timed out", "TimeoutError"));
    const response = await pending;
    expect(response.status).toBe(502);
    expect(cancel).toHaveBeenCalledOnce();
    expect(MOCKS.create).not.toHaveBeenCalled();
  } finally {
    timeout.mockRestore();
  }
});

it("preserves a started admission and its replay result after request abort", async () => {
  const controller = new AbortController();
  const admission = Promise.withResolvers<{ data: string; ok: true; replayed: boolean }>();
  MOCKS.create.mockReturnValueOnce(admission.promise);
  const pending = POST(
    new Request(URL, {
      method: "POST",
      headers: HEADERS,
      body: "{}",
      signal: controller.signal,
    }),
    { params: Promise.resolve({}) },
  );
  await vi.waitFor(() => {
    expect(MOCKS.create).toHaveBeenCalledExactlyOnceWith("{}", KEY);
  });

  controller.abort();
  admission.resolve({ ok: true, data: '{"id":"task"}', replayed: true });
  const response = await pending;
  expect(response.status).toBe(200);
  expect(response.headers.get("idempotency-replayed")).toBe("true");
  await expect(response.text()).resolves.toBe('{"id":"task"}');
  expect(MOCKS.create).toHaveBeenCalledOnce();
});

it("issues an upload through the authenticated limited action", async () => {
  const response = await POST(
    new Request(`${URL}/uploads`, { method: "POST", headers: HEADERS, body: "{}" }),
    {
      params: Promise.resolve({ path: ["uploads"] }),
    },
  );
  expect(response.status).toBe(200);
  expect(MOCKS.upload).toHaveBeenCalledOnce();
  expect(MOCKS.create).not.toHaveBeenCalled();
});

it("forwards exact body/KEY through existing authenticated rate-limited operation", async () => {
  const body = '{"source":{"content":"private text"}}';
  const response = await POST(new Request(URL, { method: "POST", headers: HEADERS, body }), {
    params: Promise.resolve({}),
  });
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(MOCKS.create).toHaveBeenCalledExactlyOnceWith(body, KEY);
});

it("exposes only the typed replay bit on a successful submission", async () => {
  MOCKS.create.mockResolvedValue({ ok: true, data: '{"id":"task"}', replayed: true });
  const response = await POST(new Request(URL, { method: "POST", headers: HEADERS, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.headers.get("idempotency-replayed")).toBe("true");
  await expect(response.text()).resolves.toBe('{"id":"task"}');
});

it("returns allowlisted reload guidance for a source revision conflict", async () => {
  MOCKS.create.mockResolvedValue({
    code: "document_transform_source_changed",
    error: "private upstream details",
    ok: false,
    status: 409,
  });
  const response = await POST(new Request(URL, { method: "POST", headers: HEADERS, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.status).toBe(409);
  await expect(response.json()).resolves.toEqual({
    code: "document_transform_source_changed",
    error: "The document changed since polishing was requested. Reload it before trying again.",
  });
});

it("keeps unrelated conflict details and codes redacted", async () => {
  MOCKS.create.mockResolvedValue({
    error: "This document already has an active transformation",
    ok: false,
    status: 409,
  });
  const response = await POST(new Request(URL, { method: "POST", headers: HEADERS, body: "{}" }), {
    params: Promise.resolve({}),
  });

  expect(response.status).toBe(409);
  await expect(response.json()).resolves.toEqual({
    error: "Document transform request could not be completed",
  });
});

it("does not expose a source-change code on a non-conflict response", async () => {
  MOCKS.create.mockResolvedValue({
    code: "document_transform_source_changed",
    error: "private upstream details",
    ok: false,
    status: 503,
  });
  const response = await POST(new Request(URL, { method: "POST", headers: HEADERS, body: "{}" }), {
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
    new Request(URL, { method: "POST", headers: { ...HEADERS, ...override }, body: "{}" }),
    { params: Promise.resolve({}) },
  );
  expect(response.status).toBeGreaterThanOrEqual(400);
  expect(MOCKS.create).not.toHaveBeenCalled();
});

it("never proxies an arbitrary path", async () => {
  const response = await GET(new Request(URL, { headers: HEADERS }), {
    params: Promise.resolve({ path: ["secrets", KEY] }),
  });
  expect(response.status).toBe(404);
  expect(FETCH_MOCK).not.toHaveBeenCalled();
});

it("looks up only a validated KEY with server-side authentication", async () => {
  FETCH_MOCK.mockResolvedValue(Response.json({ task_id: KEY, status: "pending" }));
  const response = await GET(new Request(URL, { headers: HEADERS }), {
    params: Promise.resolve({ path: ["admissions", KEY] }),
  });
  expect(response.status).toBe(200);
  const [url, options] = FETCH_MOCK.mock.calls[0] ?? [];
  expect(url).toContain(`/document-transforms/admissions/${KEY}`);
  expect(options?.headers).toMatchObject({ authorization: "Bearer private-token" });
});

it("forwards only a bounded file and supported mode to the current-polish read", async () => {
  const response = await GET(
    new Request(`${URL}/current?file_id=doc-1&mode=both`, { headers: HEADERS }),
    {
      params: Promise.resolve({ path: ["current"] }),
    },
  );

  expect(response.status).toBe(200);
  const [url] = FETCH_MOCK.mock.calls[0] ?? [];
  expect(url).toContain("/document-transforms/current?file_id=doc-1&mode=both");
  await expect(response.json()).resolves.toEqual({ result: null });
});

it.each([`${URL}/current?file_id=&mode=both`, `${URL}/current?file_id=doc-1&mode=unknown`])(
  "rejects an invalid current-polish query before fetching: %s",
  async (url) => {
    const response = await GET(new Request(url, { headers: HEADERS }), {
      params: Promise.resolve({ path: ["current"] }),
    });

    expect(response.status).toBe(400);
    expect(FETCH_MOCK).not.toHaveBeenCalled();
  },
);

it("redacts upstream exception details", async () => {
  MOCKS.create.mockRejectedValue(new Error("private draft or token"));
  const response = await POST(new Request(URL, { method: "POST", headers: HEADERS, body: "{}" }), {
    params: Promise.resolve({}),
  });
  expect(response.status).toBe(502);
  await expect(response.text()).resolves.not.toContain("private draft");
});

it("rejects missing Origin without trusting a forged content length", async () => {
  const missingOrigin = new Headers(HEADERS);
  missingOrigin.delete("origin");
  const response = await POST(
    new Request(URL, { method: "POST", headers: missingOrigin, body: "{}" }),
    { params: Promise.resolve({}) },
  );
  expect(response.status).toBe(403);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(MOCKS.create).not.toHaveBeenCalled();
});

it("enforces actual streamed bytes regardless of Content-Length", async () => {
  const response = await POST(
    new Request(URL, {
      method: "POST",
      headers: { ...HEADERS, "content-length": "1" },
      body: "x".repeat(4 * 1024 * 1024 + 1),
    }),
    { params: Promise.resolve({}) },
  );
  expect(response.status).toBe(413);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(MOCKS.create).not.toHaveBeenCalled();
});

it.each([{}, { "x-wiki-transform": "1", "sec-fetch-site": "cross-site" }])(
  "rejects cross-origin reads %j without CORS",
  async (requestHeaders) => {
    const response = await GET(new Request(URL, { headers: requestHeaders }), {
      params: Promise.resolve({ path: ["admissions", KEY] }),
    });
    expect(response.status).toBe(403);
    expect(response.headers.get("access-control-allow-origin")).toBeNull();
    expect(FETCH_MOCK).not.toHaveBeenCalled();
  },
);

it("does not forward admissions for an unauthenticated caller", async () => {
  MOCKS.cookies.mockResolvedValue({ get: vi.fn() });
  const response = await GET(new Request(URL, { headers: HEADERS }), {
    params: Promise.resolve({ path: ["admissions", KEY] }),
  });
  expect(response.status).toBe(401);
  expect(FETCH_MOCK).not.toHaveBeenCalled();
});

it.each(["capabilities", "current", "tasks", "admissions"])(
  "streams %s before the upstream body finishes without copying provider headers",
  async (path) => {
    let finishBody: (() => void) | undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{"));
        finishBody = () => {
          controller.enqueue(new TextEncoder().encode("}"));
          controller.close();
        };
      },
    });
    FETCH_MOCK.mockResolvedValue(
      new Response(body, {
        headers: { "server-timing": "app;dur=2.0", "set-cookie": "provider=private" },
      }),
    );
    const parts = path === "tasks" || path === "admissions" ? [path, KEY] : [path];
    const url = path === "current" ? `${URL}/current?file_id=doc-1&mode=both` : URL;

    const response = await GET(new Request(url, { headers: HEADERS }), {
      params: Promise.resolve({ path: parts }),
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("server-timing")).toBe("app;dur=2.0");
    expect(response.headers.get("set-cookie")).toBeNull();
    finishBody?.();
    await expect(response.json()).resolves.toEqual({});
  },
);

it("propagates transform disconnects and redacts upstream fetch failures", async () => {
  const controller = new AbortController();
  const upstream = Promise.withResolvers<Response>();
  FETCH_MOCK.mockReturnValue(upstream.promise);
  const pending = GET(new Request(URL, { headers: HEADERS, signal: controller.signal }), {
    params: Promise.resolve({ path: ["tasks", KEY] }),
  });
  await vi.waitFor(() => {
    expect(FETCH_MOCK).toHaveBeenCalledOnce();
  });

  controller.abort();
  expect(FETCH_MOCK.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  upstream.reject(new Error("private token or draft"));
  const response = await pending;
  expect(response.status).toBe(502);
  await expect(response.text()).resolves.not.toContain("private");
});
