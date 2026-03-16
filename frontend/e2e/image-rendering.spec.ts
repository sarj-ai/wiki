import { test, expect } from "@playwright/test";

test.describe("Image rendering (Fix 7)", () => {
  test("Images have lazy loading and async decoding attributes", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Navigate to a page
    await page.locator("a[href*='/docs/']").first().click();
    await page.waitForLoadState("networkidle");

    // Find images inside the document viewer
    const docImages = page.locator(".doc-viewer img, article img");
    const imageCount = await docImages.count();

    if (imageCount > 0) {
      // Check each image for lazy loading and async decoding
      for (let i = 0; i < imageCount; i++) {
        const img = docImages.nth(i);
        await expect(img).toHaveAttribute("loading", "lazy");
        await expect(img).toHaveAttribute("decoding", "async");
      }
    }
  });

  test("Mixed image+text paragraphs use div not p as container", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    await page.locator("a[href*='/docs/']").first().click();
    await page.waitForLoadState("networkidle");

    // Images inside <p> tags would be invalid HTML and cause rendering issues
    // Check that no <p> tag directly contains <img>
    const invalidNesting = await page
      .locator(".doc-viewer p > img, article p > img")
      .count();
    expect(invalidNesting).toBe(0);
  });

  test("Images have block display", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    await page.locator("a[href*='/docs/']").first().click();
    await page.waitForLoadState("networkidle");

    const docImages = page.locator(".doc-viewer img, article img");
    const imageCount = await docImages.count();

    if (imageCount > 0) {
      for (let i = 0; i < imageCount; i++) {
        const img = docImages.nth(i);
        const display = await img.evaluate(
          (el) => window.getComputedStyle(el).display
        );
        expect(display).toBe("block");
      }
    }
  });
});
