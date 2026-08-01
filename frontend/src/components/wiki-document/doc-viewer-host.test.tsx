import { describe, expect, it } from "vitest";

import { HTML_IFRAME_SANDBOX, htmlContentUrl } from "@/components/wiki-document/doc-viewer-host";

describe("isolated Drive HTML viewer", () => {
  it("grants only scripts and never injects Drive HTML into trusted React markup", () => {
    expect(HTML_IFRAME_SANDBOX).toBe("allow-scripts");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-same-origin");
    expect(htmlContentUrl("file/id", "v1")).toContain("/files/file%2Fid/content?version=v1");
  });
});
