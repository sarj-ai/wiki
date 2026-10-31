import { render, type RenderResult, screen, within } from "@testing-library/react";

import { DEMO_FILES, demoFileFor, demoHref } from "./demo-data";
import { DemoFileContext, DemoToolbar } from "./demo-toolbar";

describe("public file actions and processing disclosure", () => {
  let view: RenderResult | undefined;

  afterEach(() => {
    view?.unmount();
    view = undefined;
  });

  it("uses the configured export without exposing a private mutation", () => {
    const file = {
      ...demoFileFor(null),
      downloadUrl: "https://docs.google.com/spreadsheets/d/public-sheet/export?format=xlsx",
    };
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    const download = screen.getByRole("link", { name: `Download ${file.name}` });
    expect(download).toHaveAttribute("href", file.downloadUrl);
    expect(download).toHaveAttribute("target", "_blank");
    expect(download).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("button", { name: /Polish|Delete|Edit/ })).toBeNull();
  });

  it("omits an unsafe download instead of rendering a private API link", () => {
    const file = { ...demoFileFor(null), downloadUrl: "/api/wiki/files/private-id" };
    view = render(<DemoToolbar file={file} onReload={vi.fn()} />);
    expect(screen.queryByRole("link", { name: `Download ${file.name}` })).toBeNull();
  });

  it("renders the file's exact processing disclosure and configured comparison pair", () => {
    const before = demoFileFor(null);
    const after = DEMO_FILES.find((file) => file.id !== before.id);
    if (!after) throw new Error("A comparison needs two different public fixtures");
    const file = {
      ...after,
      comparison: { afterId: after.id, beforeId: before.id },
      processingDisclosure: "Shared spreadsheet Unslop engine; review the recorded changes.",
    };
    view = render(<DemoFileContext file={file} />);
    expect(screen.getByText(file.processingDisclosure)).toBeInTheDocument();
    expect(screen.getByText(file.description)).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Compare document versions" });
    expect(within(group).getByRole("link", { name: "Before" })).toHaveAttribute(
      "href",
      demoHref(before),
    );
    expect(within(group).getByRole("link", { name: "After" })).toHaveAttribute(
      "href",
      demoHref(after),
    );
    expect(within(group).getByRole("link", { name: "After" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps a recorded product review warning visible beside the result", () => {
    const file = {
      ...demoFileFor(null),
      processingDisclosure:
        "Wiki Unslop completed with 0 text edits. 15 rich-text areas could not be processed.",
      processingNeedsReview: true,
    };
    view = render(<DemoFileContext file={file} />);
    const warning = screen.getByRole("status");
    expect(warning).toBeVisible();
    expect(warning).toHaveTextContent("Review required.");
    expect(warning).toHaveTextContent(file.processingDisclosure);
  });

  it("offers the optional processing record as a separate public read", () => {
    const file = {
      ...demoFileFor(null),
      processingRecordFilename: "nightingale-processing.json",
    };
    view = render(<DemoFileContext file={file} />);
    const record = screen.getByRole("link", { name: "Processing record" });
    expect(record).toHaveAttribute("href", "/demo/files/nightingale-processing.json");
    expect(record).toHaveAttribute("target", "_blank");
    expect(record).toHaveAttribute("rel", "noopener noreferrer");
  });
});
