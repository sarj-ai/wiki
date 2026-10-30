import { readdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const BANNER = "// @ts-nocheck — vendored hey-api runtime; see scripts/patch-generated.ts\n";

await Promise.all(
  ["src/lib/generated/client", "src/lib/generated/core"].map(async (dir) => {
    const entries = await readdir(dir);
    await Promise.all(
      entries
        .filter((entry) => entry.endsWith(".ts"))
        .map(async (entry) => {
          const path = join(dir, entry);
          const content = await readFile(path, "utf8");
          if (content.startsWith("// @ts-nocheck")) return;
          await writeFile(path, BANNER + content);
        }),
    );
  }),
);
