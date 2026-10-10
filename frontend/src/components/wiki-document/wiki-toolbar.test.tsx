import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { MIME_DOC, MIME_MARKDOWN, MIME_PDF, MIME_PPTX, MIME_SLIDE } from "@/types/wiki";

import { WikiToolbar } from "./wiki-toolbar";

vi.mock("./read-only-short-link", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  ReadOnlyShortLink: ({ fileId }: { fileId: string }) => `reader:${fileId}`,
}));
vi.mock("./short-link-dialog", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  ShortLinkDialog: ({ fileId }: { fileId: string }) => `editor:${fileId}`,
}));
vi.mock("./reorganize-document", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  ReorganizeDocument: ({ fileId }: { fileId: string }) => `reorganize:${fileId}`,
}));
vi.mock("./lazy-document-polish-dialog", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  LazyDocumentPolishDialog: ({ source }: { source: { allowInPlace: boolean; fileId: string } }) =>
    `polish:${source.fileId}:in-place-${String(source.allowInPlace)}`,
}));

describe("WikiToolbar", () => {
  it("routes readers to the read-only short-link view", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit={false}
        driveId="drive"
        fileId="read-only-file"
        mimeType={MIME_DOC}
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Read-only document"
      />,
    );

    expect(view).toContain("reader:read-only-file");
    expect(view).not.toContain("editor:read-only-file");
    expect(view).not.toContain("reorganize:read-only-file");
  });

  it("routes editors to short-link management", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit
        driveId="drive"
        fileId="editable-file"
        mimeType={MIME_DOC}
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Editable document"
      />,
    );

    expect(view).toContain("editor:editable-file");
    expect(view).not.toContain("reader:editable-file");
    expect(view).toContain("reorganize:editable-file");
  });

  it("offers filename-based organization for non-Google Docs", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit
        driveId="drive"
        fileId="pdf-file"
        mimeType={MIME_PDF}
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Quarterly Report.pdf"
      />,
    );

    expect(view).toContain("reorganize:pdf-file");
    expect(view).not.toContain("polish:pdf-file");
  });

  it("offers safe copy polishing for a revisioned Google Doc", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit
        driveId="drive"
        fileId="doc-file"
        mimeType={MIME_DOC}
        modifiedTime="2026-09-03T12:00:00Z"
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Technical review"
      />,
    );

    expect(view).toContain("Polish");
    expect(view).not.toContain("polish:doc-file:in-place-true");
  });

  it("never offers in-place polishing for a read-only Google Doc", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit={false}
        driveId="drive"
        fileId="read-only-doc"
        mimeType={MIME_DOC}
        modifiedTime="2026-09-03T12:00:00Z"
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Read-only review"
      />,
    );

    expect(view).not.toContain("polish:read-only-doc:in-place-false");
  });

  it("offers the same copy-safe action for Drive-hosted Markdown", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit={false}
        driveId="drive"
        fileId="markdown-file"
        mimeType={MIME_MARKDOWN}
        modifiedTime="2026-09-03T12:00:00Z"
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Review.md"
      />,
    );

    expect(view).not.toContain("polish:markdown-file:in-place-false");
  });

  it.each([MIME_SLIDE, MIME_PPTX])("offers copy-safe presentation polishing for %s", (mimeType) => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit
        driveId="drive"
        fileId="deck-file"
        mimeType={mimeType}
        modifiedTime="2026-09-04T12:00:00Z"
        onReload={vi.fn()}
        parentFolderId="folder"
        title="Quarterly review"
      />,
    );

    expect(view).not.toContain("polish:deck-file:in-place-false");
  });
  it.each([
    { mimeType: MIME_DOC, canEdit: true, expected: true },
    { mimeType: MIME_DOC, canEdit: false, expected: false },
    { mimeType: MIME_MARKDOWN, canEdit: true, expected: false },
    { mimeType: MIME_SLIDE, canEdit: true, expected: false },
    { mimeType: MIME_PPTX, canEdit: true, expected: false },
  ])(
    "loads polishing on demand with the correct in-place authority: $mimeType / $canEdit",
    ({ mimeType, canEdit, expected }) => {
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      flushSync(() =>
        root.render(
          <WikiToolbar
            breadcrumb={[]}
            canEdit={canEdit}
            driveId="drive"
            fileId="deferred-file"
            mimeType={mimeType}
            modifiedTime="2026-09-03T12:00:00Z"
            onReload={vi.fn()}
            parentFolderId="folder"
            title="Review"
          />,
        ),
      );
      try {
        expect(host.textContent).not.toContain(`polish:deferred-file:in-place-${String(expected)}`);
        flushSync(() => polishButton(host).click());
        expect(host.textContent).toContain(`polish:deferred-file:in-place-${String(expected)}`);
      } finally {
        flushSync(() => root.unmount());
        host.remove();
      }
    },
  );
});

function polishButton(host: HTMLElement): HTMLButtonElement {
  const button = [...host.querySelectorAll("button")].find((element) =>
    /Polish (?:document|presentation)/u.test(element.getAttribute("aria-label") ?? ""),
  );
  if (!button) throw new Error("Polish action is missing");
  return button;
}
