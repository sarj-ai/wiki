import type { Route } from "next";

export const REDIRECT_KEY = "sarj-redirect";

export const QUERY_CACHE_KEY = "sarj-wiki-query-cache";

export function sanitizeRedirectPath(path: string): Route {
  return isSafeRoute(path) ? path : "/";
}

function isSafeRoute(path: string): path is Route {
  return (
    path.startsWith("/") && !path.startsWith("//") && !path.includes("\\") && !path.includes("://")
  );
}
