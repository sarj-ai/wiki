import { MIME_DOC, MIME_SHEET } from "@/types/wiki";

import { DEMO_FILES, demoFileFor, demoHref } from "./demo-data";

describe("public demo file selection", () => {
  it("opens the AI example Before and offers native files and before-and-after examples", () => {
    expect(DEMO_FILES.map((file) => [file.id, file.name])).toEqual([
      ["unslop-ai-before", "AI example · Before"],
      ["unslop-ai", "AI example · After"],
      ["ai-draft", "AI draft"],
      ["latency-workbook", "Sheets Demo"],
      ["nyc-weekend", "NYC weekend · Before and After"],
    ]);
    expect(demoFileFor(null).id).toBe("unslop-ai-before");
    expect(DEMO_FILES.map((file) => file.mimeType)).toEqual([
      MIME_DOC,
      MIME_DOC,
      MIME_DOC,
      MIME_SHEET,
      MIME_DOC,
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
    "resolves an unknown selector to the AI example Before: %s",
    (selector) => {
      const selected = demoFileFor(selector);
      expect(selected).toBe(demoFileFor(null));
      expect(DEMO_FILES).toContain(selected);
      expect(demoHref(selected)).not.toContain(encodeURIComponent(selector));
    },
  );
});
