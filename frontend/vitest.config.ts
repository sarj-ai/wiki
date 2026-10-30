import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
      // server-only throws outside an RSC render.
      "server-only": path.resolve(import.meta.dirname, "./src/test-stubs/empty.ts"),
    },
  },
  test: {
    benchmark: { include: ["src/**/*.bench.ts"] },
    coverage: {
      exclude: ["src/**/*.test.{ts,tsx}", "src/**/__tests__/**"],
      include: ["src/**/*.{ts,tsx}"],
      provider: "v8",
      reporter: ["text", "html"],
    },
    environment: "happy-dom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    // Bound Happy DOM setup and large-picker work during the complete local CI mirror.
    maxWorkers: 2,
    setupFiles: ["./vitest.setup.ts"],
  },
});
