import { afterEach, describe, expect, it, vi } from "vitest";

const releaseSha = "0123456789abcdef0123456789abcdef01234567";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("frontend environment", () => {
  it("freezes a valid immutable release SHA", async () => {
    vi.stubEnv("NEXT_PUBLIC_RELEASE_SHA", releaseSha);

    const { frontendReleaseSha } = await import("@/lib/env");

    expect(frontendReleaseSha()).toBe(releaseSha);
  });

  it.each(["", "dev", "ABCDEF0123456789ABCDEF0123456789ABCDEF01", "a".repeat(39)])(
    "rejects an invalid release identity: %s",
    async (revision) => {
      vi.stubEnv("NEXT_PUBLIC_RELEASE_SHA", revision);

      const { frontendReleaseSha } = await import("@/lib/env");

      expect(frontendReleaseSha()).toBeUndefined();
    },
  );
});
