import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { displayFileName, renderMimeIcon } from "@/lib/file-icons";
import { MIME_HTML } from "@/types/wiki";

describe("file presentation", () => {
  it("shows a dedicated HTML code icon", () => {
    const view = renderToStaticMarkup(renderMimeIcon(MIME_HTML));

    expect(view).toContain("lucide-file-code-corner");
  });

  it("hides HTML suffixes without changing unrelated names", () => {
    expect(displayFileName("Organization Chart.html", MIME_HTML)).toBe("Organization Chart");
    expect(displayFileName("Prototype.HTM", MIME_HTML)).toBe("Prototype");
    expect(displayFileName("Notes.txt", "text/plain")).toBe("Notes.txt");
  });

  it.each([
    ["application/zip", "lucide-archive"],
    ["audio/mpeg", "lucide-file-headphone"],
    ["text/csv", "lucide-table"],
    ["video/mp4", "lucide-file-play"],
  ])("uses a specific icon for %s", (mimeType, iconClass) => {
    expect(renderToStaticMarkup(renderMimeIcon(mimeType))).toContain(iconClass);
  });
});
