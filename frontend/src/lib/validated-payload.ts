import { type CamelCaseKeys } from "camelcase-keys";
import { type z } from "zod";

import { camelizeParsed } from "@/lib/camelize";

/** Thrown (dev/test only) when a payload fails Zod validation. */
export class ApiValidationError extends Error {
  constructor(endpoint: string, issues: string) {
    super(`Response from ${endpoint} failed schema validation:\n${issues}`);
    this.name = "ApiValidationError";
  }
}

/**
 * Validate a RAW snake_case payload against a generated schema, then
 * camelize the raw payload — not Zod's parse output, so unknown keys
 * survive and ``.default()`` backfill can't mask drift. Mismatches
 * throw in dev/test, log-only in prod. Shared by the ky client
 * (``validatedJson``) and the RSC fetcher (``serverGet``).
 */
export function validateAndCamelize<S extends z.ZodType<Record<string, unknown> | unknown[]>>(
  raw: unknown,
  schema: S,
  source: string,
): CamelCaseKeys<z.infer<S>, true> {
  const result = schema.safeParse(raw);
  if (!result.success) {
    const summary = result.error.issues
      .map((issue) => `${issue.path.join(".")}: ${issue.message}`)
      .join("\n");
    if (process.env.NODE_ENV !== "production") {
      throw new ApiValidationError(source, summary);
    }
    console.error(`API response drift at ${source}:\n${summary}`);
  }
  return camelizeParsed(raw) as CamelCaseKeys<z.infer<S>, true>;
}
