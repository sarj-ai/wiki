import { createConfig } from "./oxfmt.strict.mts";

export default createConfig({
  ignorePatterns: [
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    "playwright-report/**",
    "test-results/**",
    ".open-next/**",
    "*.lock",
    "*.tsbuildinfo",
    "package-lock.json",
    "openapi.json",
    "public/brand/sarj-loader-12132f41.svg",
    "public/brand/sarj-loader-still-12132f41.svg",
  ],
});
