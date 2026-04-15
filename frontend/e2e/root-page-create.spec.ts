import { test, expect } from "@playwright/test";

test.describe("Root page creation & rename (Fix 2)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });
  });

  test("Click + on workspace → new page with rename input focused", async ({ page }) => {
    // Find first workspace + button
    const addBtn = page.locator('button[title*="Add page to"]').first();
    await addBtn.click();

    // A rename input should appear for the new page
    const renameInput = page.locator(".group\\/tree-item input").first();
    await expect(renameInput).toBeVisible({ timeout: 10_000 });
    await expect(renameInput).toBeFocused();

    // Cancel creation by pressing Escape
    await renameInput.press("Escape");
  });

  test("Collapsed workspace → click + → section expands with rename input", async ({ page }) => {
    // Find a workspace toggle and collapse it
    const workspaceToggle = page.locator('button[aria-label*="Toggle"]').first();
    const isExpanded = await workspaceToggle
      .locator("svg")
      .evaluate((el) => el.classList.contains("rotate-90"));

    if (isExpanded) {
      await workspaceToggle.click();
      // Wait for collapse animation
      await page.waitForTimeout(300);
    }

    // Click the + button for this workspace
    const addBtn = page.locator('button[title*="Add page to"]').first();
    await addBtn.click();

    // Rename input should be visible (section auto-expanded)
    const renameInput = page.locator(".group\\/tree-item input").first();
    await expect(renameInput).toBeVisible({ timeout: 10_000 });

    await renameInput.press("Escape");
  });

  test("Click + on My Drafts → rename input appears", async ({ page }) => {
    const addPrivateBtn = page.locator('button[title="Add private page"]');
    await addPrivateBtn.click();

    const renameInput = page.locator(".group\\/tree-item input").first();
    await expect(renameInput).toBeVisible({ timeout: 10_000 });
    await expect(renameInput).toBeFocused();

    await renameInput.press("Escape");
  });
});
