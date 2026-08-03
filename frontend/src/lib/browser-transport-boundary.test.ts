import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SOURCE_ROOT = path.resolve(import.meta.dirname, "..");
const CLIENT_DIRECT_TRANSPORT = [
  /from\s+["']@\/lib\/api-client["']/u,
  /from\s+["']@\/lib\/generated\/(?:client(?:\.gen)?|sdk\.gen)["']/u,
  /\bmakeCall\s*\(/u,
  /\bwikiApi\s*\./u,
];

describe("browser transport boundary", () => {
  it("keeps generated HTTP transport behind authenticated Server Actions", () => {
    const violations = sourceFiles(SOURCE_ROOT).flatMap((file) => {
      const source = readFileSync(file, "utf8");
      if (!/^\s*["']use client["'];/u.test(source)) return [];
      return CLIENT_DIRECT_TRANSPORT.some((pattern) => pattern.test(source))
        ? [path.relative(SOURCE_ROOT, file)]
        : [];
    });

    expect(violations).toEqual([]);
  });
});

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(entryPath);
    return /\.(?:ts|tsx)$/u.test(entry.name) && !/\.test\.(?:ts|tsx)$/u.test(entry.name)
      ? [entryPath]
      : [];
  });
}
