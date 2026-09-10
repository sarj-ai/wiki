import type { Route } from "next";

export function sanitizeRedirectPath(path: string): Route {
  return isSafeRoute(path) ? path : "/";
}

function isSafeRoute(path: string): path is Route {
  return (
    path.startsWith("/") && !path.startsWith("//") && !path.includes("\\") && !path.includes("://")
  );
}
