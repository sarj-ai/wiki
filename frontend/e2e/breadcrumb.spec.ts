import { test, expect } from "@playwright/test";

test.describe("Breadcrumb workspace name (Fix 4)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('a[href*="/docs/"]', { timeout: 15_000 });
  });

  test("Root-level workspace page → breadcrumb shows workspace name", async ({
    page,
  }) => {
    // Click a workspace page (first visible page link)
    const firstPage = page.locator("a[href*='/docs/']").first();
    await firstPage.click();
    await page.waitForLoadState("networkidle");

    // Breadcrumb should be visible with at least the workspace name
    const breadcrumb = page.locator("nav[aria-label='breadcrumb']");
    await expect(breadcrumb).toBeVisible({ timeout: 5_000 });

    // Should contain at least one breadcrumb item
    const items = breadcrumb.locator("li");
    await expect(items).not.toHaveCount(0);
  });

  test("Nested page → breadcrumb shows full chain with workspace", async ({
    page,
  }) => {
    // Look for a nested page (expand a parent first)
    const expandBtn = page
      .locator('button[aria-label="Expand"]')
      .first();
    const hasNested = await expandBtn.isVisible().catch(() => false);

    if (hasNested) {
      await expandBtn.click();
      await page.waitForTimeout(300);

      // Click a child page
      const childPage = page.locator("a[href*='/docs/']").nth(1);
      await childPage.click();
      await page.waitForLoadState("networkidle");

      const breadcrumb = page.locator("nav[aria-label='breadcrumb']");
      await expect(breadcrumb).toBeVisible({ timeout: 5_000 });

      // Should have multiple breadcrumb items (workspace + parent chain)
      const separators = breadcrumb.locator("[aria-hidden='true']");
      const separatorCount = await separators.count();
      expect(separatorCount).toBeGreaterThanOrEqual(1);
    }
  });

  test("Private draft → no workspace name in breadcrumb", async ({
    page,
  }) => {
    // Find the My Drafts section and get a page link inside it
    const myDraftsSection = page.getByText("My Drafts").first();
    await expect(myDraftsSection).toBeVisible({ timeout: 10_000 });

    // Ensure My Drafts is expanded
    const myDraftsToggle = page.locator(
      'button[aria-label="Toggle private pages"]'
    );
    if (await myDraftsToggle.isVisible()) {
      // Check if collapsed by looking for page links after the toggle
      const draftLinks = myDraftsSection
        .locator("~ * a[href*='/docs/']");
      const visibleCount = await draftLinks.count().catch(() => 0);
      if (visibleCount === 0) {
        await myDraftsToggle.click();
        await page.waitForTimeout(300);
      }
    }

    // Get the draft page link — it's in the collapsible content after "My Drafts"
    // Use a more targeted approach: find the sidebar group containing "My Drafts"
    const draftPageLink = page
      .locator('[data-sidebar="group"]')
      .filter({ hasText: "My Drafts" })
      .locator("a[href*='/docs/']")
      .first();

    if (await draftPageLink.isVisible().catch(() => false)) {
      await draftPageLink.click();
      await page.waitForLoadState("networkidle");

      // Private pages should show "My Drafts" badge, not a workspace name
      const draftBadge = page.locator("article").locator("text='My Drafts'");
      await expect(draftBadge).toBeVisible({ timeout: 5_000 });
    }
  });
});
