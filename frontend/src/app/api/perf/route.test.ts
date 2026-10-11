// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("performance ingestion", () => {
  it("bounds ingestion time even when a body sends fewer than 2 KiB and stalls", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(() => {
        controller.abort(new DOMException("Deadline exceeded", "TimeoutError"));
      }, milliseconds);
      return controller.signal;
    });
    const controller = new AbortController();
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(stream) {
        stream.enqueue(new TextEncoder().encode("{"));
      },
      cancel,
    });
    const options = {
      body,
      duplex: "half",
      headers: { cookie: "access_token=test-session" },
      method: "POST",
      signal: controller.signal,
    };
    let settled: Response | undefined;
    const pending = (async () => {
      const response = await POST(new Request("https://wiki.sarj.ai/api/perf", options));
      settled = response;
      return response;
    })();
    try {
      await vi.advanceTimersByTimeAsync(5_000);
      expect(settled?.status).toBe(408);
      expect(cancel).toHaveBeenCalledOnce();
    } finally {
      controller.abort();
      await pending;
    }
  });

  it("rejects streamed bytes over the limit before reading an unbounded body", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(2049));
      },
      cancel,
    });
    const options = {
      method: "POST",
      body,
      duplex: "half",
      headers: { cookie: "access_token=test-session", "content-length": "1" },
    };
    const response = await POST(new Request("https://wiki.sarj.ai/api/perf", options));
    expect(response.status).toBe(413);
    expect(cancel).toHaveBeenCalledOnce();
  });
  it("accepts only the bounded allow-listed schema", async () => {
    const log = vi.spyOn(console, "warn").mockImplementation(vi.fn());
    const response = await POST(
      authenticatedRequest({
        durationMs: 123.4,
        navigationId: "2bc85d65-67f1-46e9-adde-edf75ce07e53",
        phase: "iframeMount",
        visibility: "visible",
      }),
    );
    expect(response.status).toBe(204);
    expect(log).toHaveBeenCalledOnce();
    expect(log.mock.calls[0]?.[0]).not.toContain("fileId");
  });

  it.each([
    { durationMs: 1, fileId: "secret", navigationId: crypto.randomUUID(), phase: "click" },
    { durationMs: -1, navigationId: crypto.randomUUID(), phase: "click" },
    { durationMs: 1, navigationId: "not-a-uuid", phase: "click" },
  ])("rejects unsafe or invalid payloads", async (payload) => {
    const response = await POST(authenticatedRequest(payload));
    expect(response.status).toBe(400);
  });

  it("rejects requests without a session cookie", async () => {
    const response = await POST(
      new Request("https://wiki.sarj.ai/api/perf", {
        body: JSON.stringify({
          durationMs: 1,
          navigationId: crypto.randomUUID(),
          phase: "iframeLoad",
        }),
        method: "POST",
      }),
    );
    expect(response.status).toBe(401);
  });
});

function authenticatedRequest(payload: unknown): Request {
  const request = new Request("https://wiki.sarj.ai/api/perf", {
    body: JSON.stringify(payload),
    headers: { "content-type": "application/json" },
    method: "POST",
  });
  request.headers.set("cookie", "access_token=test-session");
  return request;
}
