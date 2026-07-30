import type { Route } from "next";

export const REDIRECT_KEY = "sarj-redirect";

export const QUERY_CACHE_KEY = "sarj-wiki-query-cache";

export function sanitizeRedirectPath(path: string): Route {
  if (
    typeof path !== "string" ||
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    path.includes("://")
  ) {
    return "/";
  }
  // Next.js typed routes have no runtime validator; the guards above are the
  // whole check for an internal relative path.
  // eslint-disable-next-line @typescript-eslint/consistent-type-assertions, @typescript-eslint/no-unsafe-type-assertion -- Route is a compile-time template union with no runtime representation
  return path as Route;
}
