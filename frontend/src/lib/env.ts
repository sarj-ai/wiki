import * as z from "zod";

const ZApiUrl = z.url({ protocol: /^https?$/ }).transform((value) => value.replace(/\/+$/, ""));
const ZReleaseSha = z.string().regex(/^[0-9a-f]{40}$/);

export const API_URL: string = ZApiUrl.parse(
  process.env["NEXT_PUBLIC_API_URL"] ?? "http://localhost:8000",
);
const parsedReleaseSha = ZReleaseSha.safeParse(process.env["NEXT_PUBLIC_RELEASE_SHA"]);
const FRONTEND_RELEASE_SHA: string | undefined = parsedReleaseSha.success
  ? parsedReleaseSha.data
  : undefined;

export function frontendReleaseSha(): string | undefined {
  return FRONTEND_RELEASE_SHA;
}
