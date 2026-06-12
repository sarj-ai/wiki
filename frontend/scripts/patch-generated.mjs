import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const BANNER = "// @ts-nocheck — vendored hey-api runtime; see scripts/patch-generated.mjs\n";

for (const dir of ["src/lib/generated/client", "src/lib/generated/core"]) {
  for (const entry of readdirSync(dir)) {
    if (!entry.endsWith(".ts")) continue;
    const path = join(dir, entry);
    const content = readFileSync(path, "utf8");
    if (content.startsWith("// @ts-nocheck")) continue;
    writeFileSync(path, BANNER + content);
  }
}
