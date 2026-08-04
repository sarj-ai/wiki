import { renderToStaticMarkup } from "react-dom/server";

import { MIME_DOC, MIME_PDF } from "@/types/wiki";

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
    expect(view).toContain("reorganize:read-only-file");
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
  });
});
