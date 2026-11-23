// @vitest-environment node
import nextConfig from "../../next.config";

describe("private API header precedence", () => {
  it("overrides the broad HTML policy for every API path after route headers", async () => {
    const rules = await nextConfig.headers?.();
    expect(rules).toBeDefined();
    const apiIndex =
      rules?.findIndex((rule) => rule.source === "/api/:path*") ?? -1;
    const htmlIndex =
      rules?.findIndex((rule) => rule.source.startsWith("/((?!")) ?? -1;
    expect(htmlIndex).toBeGreaterThanOrEqual(0);
    expect(apiIndex).toBeGreaterThan(htmlIndex);
    expect(rules?.[apiIndex]?.headers).toContainEqual({
      key: "Cache-Control",
      value: "private, no-store",
    });
  });

  it("denies framing pages but lets the viewer frame API documents", async () => {
    const rules = (await nextConfig.headers?.()) ?? [];
    const headersFor = (source: string) =>
      Object.fromEntries(
        rules
          .filter((rule) => rule.source === source)
          .flatMap((rule) => rule.headers)
          .map(({ key, value }) => [key, value]),
      );
    expect(
      headersFor("/((?!_next/static|_next/image|brand/).*)"),
    ).toMatchObject({
      "Content-Security-Policy": expect.stringContaining(
        "frame-ancestors 'none'",
      ),
      "Cross-Origin-Opener-Policy": "same-origin",
      "X-Frame-Options": "DENY",
    });
    expect(headersFor("/api/:path*")).toMatchObject({
      "Content-Security-Policy": expect.stringContaining(
        "frame-ancestors 'self'",
      ),
      "X-Frame-Options": "SAMEORIGIN",
    });
    for (const source of ["/", "/login", "/mcp/:path*"])
      expect(headersFor(source)["Cross-Origin-Opener-Policy"]).toBe(
        "unsafe-none",
      );
  });
});
