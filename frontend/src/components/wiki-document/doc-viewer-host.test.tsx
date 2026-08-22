import { describe, expect, it } from "vitest";

import {
  HTML_IFRAME_CLASS_NAME,
  HTML_IFRAME_SANDBOX,
  htmlContentUrl,
  viewerKey,
} from "@/components/wiki-document/doc-viewer-host";
import { MIME_DOC, MIME_HTML } from "@/types/wiki";

describe("isolated Drive HTML viewer", () => {
  it("grants scripts and external popups without trusted-origin access", () => {
    expect(HTML_IFRAME_SANDBOX).toBe("allow-popups allow-popups-to-escape-sandbox allow-scripts");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-same-origin");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-top-navigation");
    expect(HTML_IFRAME_CLASS_NAME).toContain("html-theme-frame");
    expect(htmlContentUrl("file/id", "v1")).toBe("/api/files/file%2Fid/content?version=v1");
  });
});

describe("pooled viewer identity", () => {
  const base = { fileId: "file-1", reloadKey: 0, title: "A file" };

  it("keeps Google previews mounted when metadata supplies a newer version", () => {
    expect(viewerKey({ ...base, mimeType: MIME_DOC })).toBe(
      viewerKey({ ...base, mimeType: MIME_DOC, version: "new-version" }),
    );
  });

  it("reloads served HTML when its content version changes", () => {
    expect(viewerKey({ ...base, mimeType: MIME_HTML })).not.toBe(
      viewerKey({ ...base, mimeType: MIME_HTML, version: "new-version" }),
    );
  });
});
