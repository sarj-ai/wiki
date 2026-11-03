import { DEMO_FILES, demoFileFor, demoHref } from "./demo-data";

describe("public demo file selection", () => {
  it("opens the overview and offers exactly one document, spreadsheet and presentation", () => {
    expect(DEMO_FILES.map((file) => [file.id, file.name])).toEqual([
      ["wiki-overview", "Overview"],
      ["wiki-statistics", "Wiki stats"],
      ["wiki-walkthrough", "How Wiki works"],
    ]);
    expect(demoFileFor(null).id).toBe("wiki-overview");
    expect(new Set(DEMO_FILES.map((file) => file.mimeType)).size).toBe(3);
  });

  it.each([
    "unknown",
    "https://docs.google.com/document/d/private-id/edit",
    "https://evil.example/preview",
    "../api/wiki/files/private-id",
    "<script>alert(1)</script>",
  ])("resolves an unknown selector to the public overview: %s", (selector) => {
    const selected = demoFileFor(selector);
    expect(selected).toBe(demoFileFor(null));
    expect(DEMO_FILES).toContain(selected);
    expect(demoHref(selected)).not.toContain(encodeURIComponent(selector));
  });
});
