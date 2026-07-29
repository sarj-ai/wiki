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
  return path as Route;
}
