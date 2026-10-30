import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/go/[alias]/route";

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

const request = new Request("https://wiki.sarj.ai/go/eng-onboarding?utm_source=slack");

describe("document short-link resolver", () => {
  it("redirects an authenticated alias to the canonical Wiki document", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ alias: "eng-onboarding", file_id: "google-file-id_123" }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(request, {
      params: Promise.resolve({ alias: "eng-onboarding" }),
    });

    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe("/d/google-file-id_123?go=eng-onboarding");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("location")).not.toContain("utm_source");
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining("/aliases/eng-onboarding"),
      expect.objectContaining({
        headers: { Authorization: "Bearer jwt-token" },
      }),
    );
  });

  it("rejects malformed aliases before authentication or backend access", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(request, {
      params: Promise.resolve({ alias: "Google_File_ID" }),
    });

    expect(response.status).toBe(404);
    expect(response.headers.get("location")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects percent-encoded aliases instead of normalizing them", async () => {
    cookieValue.token = "jwt-token";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const encodedRequest = new Request("https://wiki.sarj.ai/go/%65ng-onboarding");
    const response = await GET(encodedRequest, {
      params: Promise.resolve({ alias: "eng-onboarding" }),
    });

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never discloses a Location for expired sessions or missing aliases", async () => {
    cookieValue.token = "expired-token";
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 404 })),
    );

    const missing = await GET(request, {
      params: Promise.resolve({ alias: "eng-onboarding" }),
    });
    cookieValue.token = undefined;
    const unauthenticated = await GET(request, {
      params: Promise.resolve({ alias: "eng-onboarding" }),
    });

    expect(missing.status).toBe(404);
    expect(missing.headers.get("location")).toBeNull();
    expect(unauthenticated.status).toBe(401);
    expect(unauthenticated.headers.get("location")).toBeNull();
  });
});
