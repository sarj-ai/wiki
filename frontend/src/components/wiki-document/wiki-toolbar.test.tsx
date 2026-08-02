import { renderToStaticMarkup } from "react-dom/server";

import { MIME_DOC } from "@/types/wiki";

import { WikiToolbar } from "./wiki-toolbar";

describe("WikiToolbar", () => {
  it("does not expose short-link management without editor access", () => {
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

    expect(view).not.toContain("Short link");
  });
});
