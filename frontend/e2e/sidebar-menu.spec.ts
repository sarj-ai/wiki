import { test, expect } from "@playwright/test";

test.describe("Sidebar 3-dot menu (Fix 1)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    // Wait for sidebar to load with page items
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });
  });

  test("Rename — input stays visible after clicking Rename", async ({ page }) => {
    // Hover over first page to reveal 3-dot menu
    const firstPage = page.locator("a[href*='/docs/']").first();
    await firstPage.hover();

    // Click the 3-dot options menu
    const optionsBtn = page.locator('button[aria-label="Page options"]').first();
    await optionsBtn.click();

    // Click Rename
    await page.getByRole("menuitem", { name: "Rename" }).click();

    // Rename input should be visible and focused
    const renameInput = page.locator(".group\\/tree-item input").first();
    await expect(renameInput).toBeVisible();
    await expect(renameInput).toBeFocused();
  });

  test("Change Icon — emoji picker opens after clicking Change icon", async ({ page }) => {
    const firstPage = page.locator("a[href*='/docs/']").first();
    await firstPage.hover();

    const optionsBtn = page.locator('button[aria-label="Page options"]').first();
    await optionsBtn.click();

    // Click Change icon
    await page.getByRole("menuitem", { name: "Change icon" }).click();

    // Emoji picker popover should appear (rendered inside a PopoverContent)
    await expect(page.locator("[data-radix-popper-content-wrapper]").last()).toBeVisible({
      timeout: 5_000,
    });
  });

  test("Delete — confirmation dialog opens after clicking Delete", async ({ page }) => {
    const firstPage = page.locator("a[href*='/docs/']").first();
    await firstPage.hover();

    const optionsBtn = page.locator('button[aria-label="Page options"]').first();
    await optionsBtn.click();

    // Click Delete
    await page.getByRole("menuitem", { name: "Delete" }).click();

    // Delete confirmation dialog should appear
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Delete page")).toBeVisible();
  });

  test("After rename, new title appears in sidebar", async ({ page }) => {
    const firstPage = page.locator("a[href*='/docs/']").first();
    const originalTitle = await firstPage.textContent();

    await firstPage.hover();

    const optionsBtn = page.locator('button[aria-label="Page options"]').first();
    await optionsBtn.click();
    await page.getByRole("menuitem", { name: "Rename" }).click();

    const renameInput = page.locator(".group\\/tree-item input").first();
    const newTitle = `Renamed ${Date.now()}`;
    await renameInput.fill(newTitle);
    await renameInput.press("Enter");

    // Sidebar should now show the new title
    await expect(page.locator(`text="${newTitle}"`)).toBeVisible({
      timeout: 5_000,
    });

    // Restore original title
    if (originalTitle) {
      const renamedPage = page.locator("a[href*='/docs/']").first();
      await renamedPage.hover();
      const btn = page.locator('button[aria-label="Page options"]').first();
      await btn.click();
      await page.getByRole("menuitem", { name: "Rename" }).click();
      const input = page.locator(".group\\/tree-item input").first();
      await input.fill(originalTitle.trim());
      await input.press("Enter");
    }
  });
});
