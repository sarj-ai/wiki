import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/health/route";

const releaseSha = "0123456789abcdef0123456789abcdef01234567";
const releaseIdentity = vi.hoisted<{ value: string | undefined }>(() => ({ value: undefined }));

vi.mock("@/lib/env", () => ({
  frontendReleaseSha: () => releaseIdentity.value,
}));

afterEach(() => {
  releaseIdentity.value = undefined;
});

describe("frontend release health", () => {
  it("reports the exact immutable release without allowing caches", async () => {
    releaseIdentity.value = releaseSha;
    const response = GET(
      new Request("https://wiki.sarj.ai/health", {
        headers: { "X-Request-ID": "frontend-canary_1" },
      }),
    );

    await expect(response.json()).resolves.toEqual({ revision: releaseSha, status: "ok" });
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(response.headers.get("x-request-id")).toBe("frontend-canary_1");
  });

  it("fails closed when the release identity is missing", async () => {
    const response = GET(new Request("https://wiki.sarj.ai/health"));

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({ status: "error" });
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it.each(["contains spaces", "bad@id", "a".repeat(65)])(
    "does not reflect an invalid request ID: %s",
    (requestId) => {
      releaseIdentity.value = releaseSha;

      const response = GET(
        new Request("https://wiki.sarj.ai/health", { headers: { "X-Request-ID": requestId } }),
      );
      const generated = response.headers.get("x-request-id");

      expect(generated).not.toBe(requestId);
      expect(generated).toMatch(/^[0-9a-f-]{36}$/);
    },
  );
});
