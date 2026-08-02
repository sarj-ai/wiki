import { renderToStaticMarkup } from "react-dom/server";

import { MIME_DOC } from "@/types/wiki";

import { WikiToolbar } from "./wiki-toolbar";

vi.mock("./read-only-short-link", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  ReadOnlyShortLink: ({ fileId }: { fileId: string }) => `reader:${fileId}`,
}));
vi.mock("./short-link-dialog", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  ShortLinkDialog: ({ fileId }: { fileId: string }) => `editor:${fileId}`,
}));

describe("WikiToolbar", () => {
  it("routes readers to the read-only short-link view", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit={false}
        fileId="read-only-file"
        mimeType={MIME_DOC}
        onReload={vi.fn()}
        title="Read-only document"
      />,
    );

    expect(view).toContain("reader:read-only-file");
    expect(view).not.toContain("editor:read-only-file");
  });

  it("routes editors to short-link management", () => {
    const view = renderToStaticMarkup(
      <WikiToolbar
        breadcrumb={[]}
        canEdit
        fileId="editable-file"
        mimeType={MIME_DOC}
        onReload={vi.fn()}
        title="Editable document"
      />,
    );

    expect(view).toContain("editor:editable-file");
    expect(view).not.toContain("reader:editable-file");
  });
});
