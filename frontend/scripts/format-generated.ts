import { readdir, readFile, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";

import { format } from "oxfmt";

import config from "../oxfmt.config.mts";
import { formatExtensions } from "../oxfmt.strict.mts";

const EXTENSIONS: ReadonlySet<string> = new Set(formatExtensions);
const { ignorePatterns: _ignorePatterns, ...options } = config;

export async function formatGenerated(directory: string): Promise<void> {
  const entries = await readdir(directory, {
    recursive: true,
    withFileTypes: true,
  });
  await Promise.all(
    entries
      .filter(
        (entry) =>
          entry.isFile() && EXTENSIONS.has(extname(entry.name).slice(1)),
      )
      .map((entry) => formatFile(join(entry.parentPath, entry.name))),
  );
}

async function formatFile(path: string): Promise<void> {
  const source = await readFile(path, "utf8");
  const result = await format(path, source, options);
  if (result.errors.length > 0)
    throw new Error(
      `Cannot format ${path}: ${result.errors.map((error) => error.message).join("\n")}`,
    );
  if (result.code !== source) await writeFile(path, result.code, "utf8");
}

if (import.meta.main)
  await formatGenerated(process.argv[2] ?? "src/lib/generated");
