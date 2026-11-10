import { MIME_DOC, MIME_SHEET, MIME_SLIDE } from "@/types/wiki";

import { DEMO_FILES, demoFileFor, demoHref } from "./demo-data";

describe("public demo file selection", () => {
  it("opens Numbers Every Programmer Should Know and offers the four native files in one Drive", () => {
    expect(DEMO_FILES.map((file) => [file.id, file.name])).toEqual([
      ["latency-guide", "Numbers Every Programmer Should Know"],
      ["ai-draft", "AI draft"],
      ["latency-workbook", "Numbers workbook"],
      ["latency-slides", "Numbers slides"],
    ]);
    expect(demoFileFor(null).id).toBe("latency-guide");
    expect(DEMO_FILES.map((file) => file.mimeType)).toEqual([
      MIME_DOC,
      MIME_DOC,
      MIME_SHEET,
      MIME_SLIDE,
    ]);
  });

  it.each(DEMO_FILES)("selects the configured $id row", (file) => {
    expect(demoFileFor(file.id)).toBe(file);
    expect(demoHref(file)).toBe(`/demo?file=${file.id}`);
  });

  it.each([
    "unknown",
    "wiki-overview",
    "wiki-statistics",
    "wiki-walkthrough",
    "1Ks_EO_8tzXNgUKLgrXgbcOSSGAXlNWKb8zeiDRqBGEo",
    "https://docs.google.com/document/d/private-id/edit",
    "https://evil.example/preview",
    "../api/wiki/files/private-id",
    "<script>alert(1)</script>",
  ])(
    "resolves an unknown selector to Numbers Every Programmer Should Know: %s",
    (selector) => {
      const selected = demoFileFor(selector);
      expect(selected).toBe(demoFileFor(null));
      expect(DEMO_FILES).toContain(selected);
      expect(demoHref(selected)).not.toContain(encodeURIComponent(selector));
    },
  );
});
