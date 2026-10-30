import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { it } from "node:test";

const BENCHMARK_PATH = path.join(import.meta.dirname, "benchmark-bundle.ts");

it("counts unique synchronous JavaScript chunks from a Next manifest", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "wiki-bundle-"));
  try {
    await mkdir(path.join(root, "server/app"), { recursive: true });
    await mkdir(path.join(root, "static/chunks"), { recursive: true });
    await writeFile(path.join(root, "static/chunks/a b.js"), "hello");
    const chunk = "static/chunks/a%20b.js";
    const manifest = {
      page: {
        clientModules: {
          first: { chunks: [123, chunk, "styles.css"], async: false },
          repeated: { chunks: [chunk] },
          deferred: { chunks: ["missing-async.js"], async: true },
        },
      },
    };
    await writeFile(
      path.join(root, "server/app/page_client-reference-manifest.js"),
      `globalThis.__RSC_MANIFEST = ${JSON.stringify(manifest)};`,
    );
    const output = execFileSync(process.execPath, [BENCHMARK_PATH, root, "page"], {
      encoding: "utf8",
    });
    assert.deepEqual(JSON.parse(output), {
      files: [{ file: chunk, bytes: 5, gzipBytes: 25 }],
      bytes: 5,
      gzipBytes: 25,
    });
  } finally {
    await rm(root, { recursive: true });
  }
});

it("rejects a changed Next manifest shape before reading chunk files", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "wiki-bundle-"));
  try {
    await mkdir(path.join(root, "server/app"), { recursive: true });
    await writeFile(
      path.join(root, "server/app/page_client-reference-manifest.js"),
      'globalThis.__RSC_MANIFEST = { page: { clientModules: { bad: { chunks: "missing.js" } } } };',
    );
    assert.throws(
      () => execFileSync(process.execPath, [BENCHMARK_PATH, root, "page"], { stdio: "pipe" }),
      /ZodError/u,
    );
  } finally {
    await rm(root, { recursive: true });
  }
});
