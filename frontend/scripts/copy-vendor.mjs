// Copies iframe-served vendor assets out of node_modules. The doc reading view
// injects Paged.js into its sandboxed srcdoc iframe by URL — it can't be
// imported as a module there, so the dist file must be reachable under /vendor.
import { copyFileSync, mkdirSync } from "node:fs";

mkdirSync("public/vendor", { recursive: true });
copyFileSync("node_modules/pagedjs/dist/paged.polyfill.js", "public/vendor/paged.polyfill.js");
console.log("vendor assets copied: pagedjs");
