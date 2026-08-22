// @vitest-environment happy-dom

import {
  applyDocumentTitle,
  DEFAULT_DOCUMENT_TITLE,
  documentTitleFor,
} from "@/hooks/use-document-title";

describe("document title", () => {
  it("sets the settled document title and restores the generic app title", () => {
    document.title = "Stale server title";

    const reset = applyDocumentTitle("Annual Plan");

    expect(document.title).toBe(documentTitleFor("Annual Plan"));
    reset();
    expect(document.title).toBe(DEFAULT_DOCUMENT_TITLE);
  });
});
