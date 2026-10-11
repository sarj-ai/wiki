// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/files/[fileId]/content/route";

const cookieValue = vi.hoisted<{ token: string | undefined }>(() => ({ token: undefined }));

vi.mock("next/headers", () => ({
  cookies: () => ({
    get: (name: string) =>
      name === "access_token" && cookieValue.token !== undefined
        ? { name, value: cookieValue.token }
        : undefined,
  }),
}));

afterEach(() => {
  cookieValue.token = undefined;
  vi.unstubAllGlobals();
});

const request = new Request("https://wiki.sarj.ai/api/files/1234567890/content");

describe("authenticated HTML content proxy", () => {
  it("rejects unauthenticated iframe requests without calling the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(request, { params: Promise.resolve({ fileId: "1234567890" }) });

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards Bearer authentication and preserves the isolated viewer headers", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("<!doctype html><title>Chart</title>", {
        headers: {
          "cache-control": "private, max-age=300",
          "content-security-policy": "default-src 'none'; sandbox allow-scripts",
          "content-type": "text/html; charset=utf-8",
          "cross-origin-resource-policy": "same-site",
          "x-content-type-options": "nosniff",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(request, { params: Promise.resolve({ fileId: "1234567890" }) });

    expect(response.status).toBe(200);
    await expect(response.text()).resolves.toContain("<title>Chart</title>");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("content-security-policy")).toContain("sandbox allow-scripts");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.any(String),
      expect.objectContaining({ headers: { Authorization: "Bearer jwt-token" } }),
    );
  });

  it("rejects malformed Drive IDs before authentication", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(request, { params: Promise.resolve({ fileId: "../secret" }) });

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

it("does not dispatch an already cancelled HTML viewer request", async () => {
  cookieValue.token = "jwt-token";
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  controller.abort();
  const response = await GET(new Request(request.url, { signal: controller.signal }), {
    params: Promise.resolve({ fileId: "1234567890" }),
  });
  expect(response.status).toBe(502);
  expect(fetchMock).not.toHaveBeenCalled();
});

it("cancels an obsolete HTML upstream request and keeps redirect failures redacted", async () => {
  cookieValue.token = "jwt-token";
  const fetchMock = vi.fn<typeof fetch>();
  const pendingFetch = Promise.withResolvers<Response>();
  fetchMock.mockReturnValue(pendingFetch.promise);
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  const pending = GET(new Request(request.url, { signal: controller.signal }), {
    params: Promise.resolve({ fileId: "1234567890" }),
  });
  await vi.waitFor(() => {
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  expect(fetchMock.mock.calls[0]?.[1]?.redirect).toBe("manual");
  controller.abort();
  expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  pendingFetch.reject(new Error("private upstream token"));
  const response = await pending;
  expect(response.status).toBe(502);
  await expect(response.text()).resolves.not.toContain("private");
});

it("streams HTML before EOF while preserving the viewer policy", async () => {
  cookieValue.token = "jwt-token";
  let finishBody: (() => void) | undefined;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("<!doctype html>"));
      finishBody = () => {
        controller.close();
      };
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(body, {
        headers: {
          "content-security-policy": "default-src 'none'; sandbox allow-scripts",
          "set-cookie": "private",
        },
      }),
    ),
  );
  const response = await GET(request, { params: Promise.resolve({ fileId: "1234567890" }) });
  expect(response.status).toBe(200);
  expect(response.headers.get("set-cookie")).toBeNull();
  expect(response.headers.get("content-security-policy")).toContain("sandbox allow-scripts");
  finishBody?.();
  await expect(response.text()).resolves.toBe("<!doctype html>");
});
