import camelcaseKeys from "camelcase-keys";

// Single source of truth for the wire→app case transform — the client
// parseJson, the Zod boundary, and server prefetch must match shapes.
export function camelizeParsed(parsed: unknown): unknown {
  if (parsed && typeof parsed === "object") {
    return camelcaseKeys(parsed as never, { deep: true });
  }
  return parsed;
}
