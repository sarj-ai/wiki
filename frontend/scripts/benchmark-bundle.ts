import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";

import { z } from "zod";

// This is a runtime boundary: Next emits JavaScript, and its shape may change.
const ManifestSchema = z.record(
  z.string(),
  z.object({
    clientModules: z.record(
      z.string(),
      z.object({
        async: z.boolean().optional(),
        chunks: z.array(z.union([z.string(), z.number()])),
      }),
    ),
  }),
);

const BUILD_ROOT = path.resolve(process.argv[2] ?? path.resolve(import.meta.dirname, "../.next"));
const APP_ROUTE = process.argv[3] ?? "(app)/page";
const CONTEXT: Record<string, unknown> = {};
const MANIFEST_PATH = path.join(BUILD_ROOT, `server/app/${APP_ROUTE}_client-reference-manifest.js`);
vm.runInNewContext(await readFile(MANIFEST_PATH, "utf8"), CONTEXT);
const CHUNKS: ReadonlySet<string> = new Set(
  Object.values(ManifestSchema.parse(CONTEXT["__RSC_MANIFEST"])).flatMap((entry) =>
    Object.values(entry.clientModules)
      .filter((module) => !module.async)
      .flatMap((module) =>
        module.chunks.filter(
          (chunk): chunk is string => typeof chunk === "string" && chunk.endsWith(".js"),
        ),
      ),
  ),
);
const FILES = await Promise.all(
  [...CHUNKS].map(async (file) => {
    const bytes = await readFile(path.join(BUILD_ROOT, decodeURIComponent(file)));
    return { file, bytes: bytes.length, gzipBytes: zlib.gzipSync(bytes).length };
  }),
);
process.stdout.write(
  `${JSON.stringify(
    {
      files: FILES,
      bytes: FILES.reduce((total, file) => total + file.bytes, 0),
      gzipBytes: FILES.reduce((total, file) => total + file.gzipBytes, 0),
    },
    null,
    2,
  )}\n`,
);
