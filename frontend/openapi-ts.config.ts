import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "openapi.json",
  output: "src/lib/generated",
  plugins: [
    "@hey-api/typescript",
    "@hey-api/sdk",
    "@hey-api/client-fetch",
    { name: "zod", requests: false, responses: false },
  ],
});
