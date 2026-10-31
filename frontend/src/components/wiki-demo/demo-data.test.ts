import { DEMO_FILES, demoComparisonFor, demoFileFor, demoHref } from "./demo-data";

describe("public fixture selection and comparisons", () => {
  it.each([
    "unknown",
    "https://docs.google.com/document/d/private-id/edit",
    "https://evil.example/preview",
    "../api/wiki/files/private-id",
    "<script>alert(1)</script>",
  ])("resolves an unknown selector to the existing public default: %s", (selector) => {
    const selected = demoFileFor(selector);
    expect(selected).toBe(demoFileFor(null));
    expect(DEMO_FILES).toContain(selected);
    expect(demoHref(selected)).not.toContain(encodeURIComponent(selector));
  });

  it("uses the configured known fixture IDs for both comparison links", () => {
    const before = demoFileFor(null);
    const after = DEMO_FILES.find((file) => file.id !== before.id);
    if (!after) throw new Error("A comparison needs two different public fixtures");
    const file = { ...after, comparison: { afterId: after.id, beforeId: before.id } };
    expect(demoComparisonFor(file)).toEqual([
      { file: before, label: "Before" },
      { file: after, label: "After" },
    ]);
  });

  it("hides incomplete or identical comparison pairs instead of substituting the default", () => {
    const file = demoFileFor(null);
    expect(
      demoComparisonFor({ ...file, comparison: { afterId: "private-id", beforeId: file.id } }),
    ).toEqual([]);
    expect(
      demoComparisonFor({ ...file, comparison: { afterId: file.id, beforeId: file.id } }),
    ).toEqual([]);
  });
});
