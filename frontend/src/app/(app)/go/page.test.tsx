import { renderToStaticMarkup } from "react-dom/server";

import QuickLinksPage from "@/app/(app)/go/page";

const testState = vi.hoisted<{ token: string | undefined }>(() => ({ token: "token" }));
const getRecentDocumentAliases = vi.hoisted(() => vi.fn());

vi.mock("next/headers", () => ({
  cookies: () => ({
    get: () => (testState.token === undefined ? undefined : { value: testState.token }),
  }),
}));

vi.mock("@/lib/alias-client", () => ({
  getRecentDocumentAliases,
}));

describe("QuickLinksPage", () => {
  it("renders only the permission-filtered aliases returned by the server", async () => {
    getRecentDocumentAliases.mockResolvedValue({
      aliases: [
        {
          alias: "accessible-page",
          driveName: "Team Drive",
          fileId: "file-1",
          lastVisitedAt: "2026-08-04T15:00:00Z",
          mimeType: "application/vnd.google-apps.document",
          name: "Accessible Page",
        },
      ],
      status: "found",
    });

    const view = renderToStaticMarkup(await QuickLinksPage());

    expect(view).toContain("Accessible Page");
    expect(view).toContain('href="/go/accessible-page"');
    expect(view).not.toContain("file-1");
  });

  it("fails closed when the server cannot load recent aliases", async () => {
    getRecentDocumentAliases.mockResolvedValue({ status: "unavailable" });

    const view = renderToStaticMarkup(await QuickLinksPage());

    expect(view).toContain("Unable to load quick links");
    expect(view).not.toContain("href=");
  });
});
