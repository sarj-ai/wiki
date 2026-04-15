import { test, expect } from "@playwright/test";

test.describe("Empty TOC (Fix 8)", () => {
  test("Page with empty TOC → no .doc-toc element rendered", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Navigate to pages and check for empty TOC elements
    const pageLinks = page.locator("a[href*='/docs/']");
    const linkCount = await pageLinks.count();

    // Check first few pages for empty TOC handling
    const pagesToCheck = Math.min(linkCount, 3);

    for (let i = 0; i < pagesToCheck; i++) {
      await pageLinks.nth(i).click();
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(500);

      // If a .doc-toc element exists, it should have actual content
      const tocElements = page.locator(".doc-toc");
      const tocCount = await tocElements.count();

      for (let j = 0; j < tocCount; j++) {
        const toc = tocElements.nth(j);
        const tocList = toc.locator(".doc-toc-list li");
        const itemCount = await tocList.count();
        // If TOC is rendered, it must have at least one entry
        expect(itemCount).toBeGreaterThan(0);
      }

      // Go back to sidebar for next page
      if (i < pagesToCheck - 1) {
        await page.goto("/");
        await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });
      }
    }
  });
});
