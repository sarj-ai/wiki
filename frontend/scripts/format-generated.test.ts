import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { it } from "node:test";

import { formatGenerated } from "./format-generated.ts";

it("formats ignored generated source recursively and preserves unsupported files", async () => {
  const root = await mkdtemp(join(tmpdir(), "wiki-generated-format-"));
  const directory = join(root, "generated");
  const nested = join(directory, "client");
  await mkdir(nested, { recursive: true });
  await writeFile(join(root, ".gitignore"), "generated/\n", "utf8");
  const path = join(nested, "types.gen.ts");
  await writeFile(path, "export const value={first:1,second:2};", "utf8");
  const binary = join(directory, "untouched.bin");
  await writeFile(binary, Uint8Array.of(0, 255, 1));
  try {
    await formatGenerated(directory);
    const formatted = await readFile(path, "utf8");
    assert.equal(formatted, "export const value = { first: 1, second: 2 };\n");
    await formatGenerated(directory);
    assert.equal(await readFile(path, "utf8"), formatted);
    assert.deepEqual(await readFile(binary), Buffer.from([0, 255, 1]));
  } finally {
    await rm(root, { recursive: true });
  }
});

it("fails on malformed generated source without rewriting it", async () => {
  const root = await mkdtemp(join(tmpdir(), "wiki-generated-format-error-"));
  const path = join(root, "broken.ts");
  const source = "export const = ;";
  await writeFile(path, source, "utf8");
  try {
    await assert.rejects(formatGenerated(root), /Cannot format/u);
    assert.equal(await readFile(path, "utf8"), source);
  } finally {
    await rm(root, { recursive: true });
  }
});
