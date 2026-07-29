import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const baselinePath = "eslint-suppressions.json";
const temporaryDirectory = await mkdtemp(join(tmpdir(), "wiki-eslint-baseline-"));
const temporaryBaseline = join(temporaryDirectory, "eslint-suppressions.json");

try {
  await copyFile(baselinePath, temporaryBaseline);
  execFileSync(
    "node_modules/.bin/eslint",
    [
      ".",
      "--suppressions-location",
      temporaryBaseline,
      "--prune-suppressions",
      "--pass-on-unpruned-suppressions",
    ],
    { stdio: "inherit" },
  );
  const [committed, pruned] = await Promise.all([
    readFile(baselinePath, "utf8"),
    readFile(temporaryBaseline, "utf8"),
  ]);
  assert.deepEqual(
    JSON.parse(pruned),
    JSON.parse(committed),
    "eslint-suppressions.json contains unused or inflated allowances",
  );
} finally {
  await rm(temporaryDirectory, { recursive: true });
}
