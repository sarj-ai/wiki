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
    expect(await response.text()).toContain("<title>Chart</title>");
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
