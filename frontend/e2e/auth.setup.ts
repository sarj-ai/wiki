/**
 * Auth storage state setup for Playwright E2E tests.
 *
 * Since the app requires Google OAuth (@sarj.ai only), we cannot automate
 * the login flow. Instead, log in manually once and save the browser state:
 *
 *   npx playwright codegen --save-storage=e2e/auth.json http://localhost:3000
 *
 * This opens a browser where you can complete the Google OAuth flow.
 * Once logged in, close the browser — cookies and localStorage are saved
 * to e2e/auth.json and reused by all tests via `storageState` in the config.
 *
 * The auth.json file is gitignored and must be regenerated when tokens expire.
 */

import { test as setup, expect } from "@playwright/test";
import * as fs from "fs";

const authFile = "e2e/auth.json";

setup("verify auth state exists", async () => {
  if (!fs.existsSync(authFile)) {
    throw new Error(
      `Missing ${authFile}. Run:\n  npx playwright codegen --save-storage=e2e/auth.json http://localhost:3000\nto create it.`
    );
  }

  const content = JSON.parse(fs.readFileSync(authFile, "utf-8"));
  expect(content.cookies?.length || content.origins?.length).toBeTruthy();
});
