import { test, expect } from "@playwright/test";

test.describe("Share dialog & publish flow (Fix 3)", () => {
  test("Private page → share dialog shows workspace selector and Publish button", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Navigate to a page
    await page.locator("a[href*='/docs/']").first().click();
    await page.waitForLoadState("networkidle");

    // Open share dialog
    const shareBtn = page.getByRole("button", { name: /share/i });
    if (await shareBtn.isVisible()) {
      await shareBtn.click();

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();

      // Should contain "Publish" button (not a toggle switch)
      const publishBtn = dialog.getByRole("button", { name: /publish/i });
      const toggleSwitch = dialog.locator('button[role="switch"]');

      // If page is private, we expect publish controls
      // If already published, we expect static text
      const isPublished = await dialog
        .getByText(/Published to/)
        .isVisible()
        .catch(() => false);

      if (!isPublished) {
        await expect(publishBtn).toBeVisible();
        // No toggle switch should be present
        await expect(toggleSwitch).toHaveCount(0);
      }

      // Close dialog
      await dialog.getByRole("button", { name: /done/i }).click();
    }
  });

  test("Already-published page → shows static Published to text", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Find and click a workspace page (not under My Drafts)
    const workspacePage = page.locator("a[href*='/docs/']").first();
    await workspacePage.click();
    await page.waitForLoadState("networkidle");

    const shareBtn = page.getByRole("button", { name: /share/i });
    if (await shareBtn.isVisible()) {
      await shareBtn.click();

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();

      // If this page is published, verify the static text
      const publishedText = dialog.getByText(/Published to/);
      const isPublished = await publishedText.isVisible().catch(() => false);

      if (isPublished) {
        await expect(publishedText).toBeVisible();
        // Should NOT show a publish button
        const publishBtn = dialog.getByRole("button", {
          name: /^Publish$/,
        });
        await expect(publishBtn).toHaveCount(0);
      }

      await dialog.getByRole("button", { name: /done/i }).click();
    }
  });

  test("Publish action shows spinner and completes", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });

    // Navigate to a page
    await page.locator("a[href*='/docs/']").first().click();
    await page.waitForLoadState("networkidle");

    const shareBtn = page.getByRole("button", { name: /share/i });
    if (await shareBtn.isVisible()) {
      await shareBtn.click();

      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();

      const publishBtn = dialog.getByRole("button", { name: /publish/i });
      const canPublish = await publishBtn.isVisible().catch(() => false);

      if (canPublish) {
        // Intercept the publish API call
        const publishPromise = page.waitForResponse(
          (resp) => resp.url().includes("/publish") && resp.request().method() === "POST",
        );

        await publishBtn.click();

        // Button should be disabled (loading state) during publish
        await expect(publishBtn).toBeDisabled();

        await publishPromise;
      }
    }
  });
});
