import { renderToStaticMarkup } from "react-dom/server";

import QuickLinksPage from "@/app/(app)/go/page";

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({ data: undefined, isError: false }),
}));

describe("QuickLinksPage", () => {
  it("renders the per-user local quick-link loader", () => {
    const view = renderToStaticMarkup(<QuickLinksPage />);

    expect(view).toContain("Loading quick links");
  });
});
