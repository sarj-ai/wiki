import { defineConfig } from "@hey-api/openapi-ts";

// Zod schemas only — the hand-written ky client and hooks stay.
// Regenerate with `npm run generate:api`; CI fails on drift.
export default defineConfig({
  input: "openapi.json",
  output: "src/lib/generated",
  plugins: [{ name: "zod", requests: false, responses: false }],
});
