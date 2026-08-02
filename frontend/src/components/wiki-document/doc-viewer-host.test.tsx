import { describe, expect, it } from "vitest";

import {
  HTML_IFRAME_CLASS_NAME,
  HTML_IFRAME_SANDBOX,
  htmlContentUrl,
} from "@/components/wiki-document/doc-viewer-host";

describe("isolated Drive HTML viewer", () => {
  it("grants scripts and external popups without trusted-origin access", () => {
    expect(HTML_IFRAME_SANDBOX).toBe("allow-popups allow-popups-to-escape-sandbox allow-scripts");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-same-origin");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-top-navigation");
    expect(HTML_IFRAME_CLASS_NAME).toContain("html-theme-frame");
    expect(htmlContentUrl("file/id", "v1")).toBe("/api/files/file%2Fid/content?version=v1");
  });
});
