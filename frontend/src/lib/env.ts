import * as z from "zod";

const ApiUrlSchema = z
  .url({ protocol: /^https?$/ })
  .transform((value) => value.replace(/\/+$/, ""));
const ReleaseShaSchema = z.string().regex(/^[0-9a-f]{40}$/);

export const API_URL: string = ApiUrlSchema.parse(
  process.env["NEXT_PUBLIC_API_URL"] ?? "http://localhost:8000",
);
const PARSED_RELEASE_SHA = ReleaseShaSchema.safeParse(process.env["NEXT_PUBLIC_RELEASE_SHA"]);
const FRONTEND_RELEASE_SHA: string | undefined = PARSED_RELEASE_SHA.success
  ? PARSED_RELEASE_SHA.data
  : undefined;

export function frontendReleaseSha(): string | undefined {
  return FRONTEND_RELEASE_SHA;
}
