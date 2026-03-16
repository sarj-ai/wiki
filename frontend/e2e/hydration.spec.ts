import { test, expect } from "@playwright/test";

test.describe("Hydration mismatch (Fix 6)", () => {
  test("Collapsed section + hard reload → no hydration errors", async ({
    page,
  }) => {
    // Collect console errors
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Collapse a workspace section
    const toggleBtn = page
      .locator('button[aria-label*="Toggle"]')
      .first();
    const isExpanded = await toggleBtn
      .locator("svg")
      .evaluate((el) => el.classList.contains("rotate-90"))
      .catch(() => false);

    if (isExpanded) {
      await toggleBtn.click();
      await page.waitForTimeout(300);
    }

    // Hard reload
    await page.reload();
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Allow time for hydration errors to surface
    await page.waitForTimeout(1_000);

    // Filter for hydration-related errors
    const hydrationErrors = consoleErrors.filter(
      (e) =>
        e.toLowerCase().includes("hydration") ||
        e.toLowerCase().includes("did not match") ||
        e.toLowerCase().includes("server rendered")
    );

    expect(hydrationErrors).toHaveLength(0);
  });

  test("Sections render correctly after reload", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Collapse a section
    const toggleBtn = page
      .locator('button[aria-label*="Toggle"]')
      .first();
    await toggleBtn.click();
    await page.waitForTimeout(300);

    // Reload
    await page.reload();
    await page.waitForSelector('button[aria-label*="Toggle"]', {
      timeout: 15_000,
    });

    // Sections should still be present and interactive
    const toggleBtns = page.locator('button[aria-label*="Toggle"]');
    const count = await toggleBtns.count();
    expect(count).toBeGreaterThan(0);

    // Click a toggle — it should respond
    await toggleBtns.first().click();
    await page.waitForTimeout(300);

    // The toggle should work without errors
    const svgRotated = await toggleBtns
      .first()
      .locator("svg")
      .evaluate((el) => el.classList.contains("rotate-90"))
      .catch(() => null);

    expect(svgRotated).not.toBeNull();
  });
});
