// @vitest-environment node
import nextConfig from "../../next.config";

describe("private API header precedence", () => {
  it("overrides the broad HTML policy for every API path after route headers", async () => {
    const rules = await nextConfig.headers?.();
    expect(rules).toBeDefined();
    const apiIndex = rules?.findIndex((rule) => rule.source === "/api/:path*") ?? -1;
    const htmlIndex = rules?.findIndex((rule) => rule.source.startsWith("/((?!")) ?? -1;
    expect(htmlIndex).toBeGreaterThanOrEqual(0);
    expect(apiIndex).toBeGreaterThan(htmlIndex);
    expect(rules?.[apiIndex]?.headers).toContainEqual({
      key: "Cache-Control",
      value: "private, no-store",
    });
  });
});
