import { renderToStaticMarkup } from "react-dom/server";

import HomePage from "@/app/(app)/page";

describe("HomePage", () => {
  it("lands on recent pages while identity is being verified", () => {
    const view = renderToStaticMarkup(<HomePage />);
    expect(view).toContain("Recent pages");
    expect(view).toContain("Loading recent pages");
  });
});
