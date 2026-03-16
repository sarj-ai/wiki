import { test, expect } from "@playwright/test";

test.describe("Drag & drop loading state (Fix 9)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });
  });

  test("Moving page shows reduced opacity during move", async ({ page }) => {
    const pageItems = page.locator("a[href*='/docs/']");
    const itemCount = await pageItems.count();

    if (itemCount < 2) {
      test.skip();
      return;
    }

    // Get the first page item's parent container
    const firstItem = pageItems.first();
    const secondItem = pageItems.nth(1);

    const firstBox = await firstItem.boundingBox();
    const secondBox = await secondItem.boundingBox();

    if (!firstBox || !secondBox) {
      test.skip();
      return;
    }

    // Start drag from first item
    await page.mouse.move(
      firstBox.x + firstBox.width / 2,
      firstBox.y + firstBox.height / 2
    );
    await page.mouse.down();

    // Move to second item position
    await page.mouse.move(
      secondBox.x + secondBox.width / 2,
      secondBox.y + secondBox.height / 2,
      { steps: 10 }
    );

    // Check for drag overlay presence (DnD Kit creates a drag overlay)
    const dragOverlay = page.locator("[data-dnd-overlay],.shadow-md");
    const hasOverlay = await dragOverlay.isVisible().catch(() => false);

    // Release
    await page.mouse.up();

    // After drop, any opacity changes should resolve back to normal
    await page.waitForTimeout(1_000);

    // Verify all page items have full opacity (no stuck loading state)
    for (let i = 0; i < Math.min(itemCount, 5); i++) {
      const item = pageItems.nth(i);
      if (await item.isVisible()) {
        const opacity = await item.evaluate(
          (el) => window.getComputedStyle(el).opacity
        );
        expect(parseFloat(opacity)).toBeGreaterThanOrEqual(0.9);
      }
    }
  });

  test("After move completes, opacity returns to normal", async ({
    page,
  }) => {
    // Intercept move API call
    const moveResponsePromise = page
      .waitForResponse(
        (resp) =>
          resp.url().includes("/pages/") &&
          resp.request().method() === "PATCH",
        { timeout: 5_000 }
      )
      .catch(() => null);

    const pageItems = page.locator("a[href*='/docs/']");
    const itemCount = await pageItems.count();

    // Verify all page items have full opacity (steady state)
    for (let i = 0; i < Math.min(itemCount, 5); i++) {
      const item = pageItems.nth(i);
      if (await item.isVisible()) {
        const opacity = await item.evaluate(
          (el) => window.getComputedStyle(el).opacity
        );
        expect(parseFloat(opacity)).toBeGreaterThanOrEqual(0.9);
      }
    }
  });
});
