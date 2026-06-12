import * as z from "zod";

const ZApiUrl = z.url({ protocol: /^https?$/ }).transform((value) => value.replace(/\/+$/, ""));

export const API_URL: string = ZApiUrl.parse(
  process.env["NEXT_PUBLIC_API_URL"] ?? "http://localhost:8000",
);
