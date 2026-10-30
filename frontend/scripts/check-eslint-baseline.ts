import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASELINE_PATH = "eslint-suppressions.json";
const TEMPORARY_DIRECTORY = await mkdtemp(join(tmpdir(), "wiki-eslint-baseline-"));
const TEMPORARY_BASELINE = join(TEMPORARY_DIRECTORY, "eslint-suppressions.json");

try {
  await copyFile(BASELINE_PATH, TEMPORARY_BASELINE);
  execFileSync(
    "node_modules/.bin/eslint",
    [
      ".",
      "--suppressions-location",
      TEMPORARY_BASELINE,
      "--prune-suppressions",
      "--pass-on-unpruned-suppressions",
    ],
    { stdio: "inherit" },
  );
  const [committed, pruned] = await Promise.all([
    readFile(BASELINE_PATH, "utf8"),
    readFile(TEMPORARY_BASELINE, "utf8"),
  ]);
  assert.deepEqual(
    JSON.parse(pruned),
    JSON.parse(committed),
    "eslint-suppressions.json contains unused or inflated allowances",
  );
} finally {
  await rm(TEMPORARY_DIRECTORY, { recursive: true });
}
