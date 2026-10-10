import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";

const buildRoot = path.resolve(process.argv[2] ?? path.resolve(import.meta.dirname, "../.next"));
const context = {};
const manifestPath = path.join(buildRoot, "server/app/(app)/page_client-reference-manifest.js");
vm.runInNewContext(await readFile(manifestPath, "utf8"), context);
const chunks = new Set(
  Object.values(context.__RSC_MANIFEST).flatMap((entry) =>
    Object.values(entry.clientModules)
      .filter((module) => !module.async)
      .flatMap((module) => module.chunks.filter((chunk) => String(chunk).endsWith(".js"))),
  ),
);
const files = await Promise.all(
  [...chunks].map(async (file) => {
    const bytes = await readFile(path.join(buildRoot, file));
    return { file, bytes: bytes.length, gzipBytes: zlib.gzipSync(bytes).length };
  }),
);
process.stdout.write(
  `${JSON.stringify(
    {
      files,
      bytes: files.reduce((total, file) => total + file.bytes, 0),
      gzipBytes: files.reduce((total, file) => total + file.gzipBytes, 0),
    },
    null,
    2,
  )}\n`,
);
