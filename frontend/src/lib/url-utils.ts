// Post-login destination, carried across the OAuth round-trip.
export const REDIRECT_KEY = "sarj-redirect";

// Persisted React Query cache (query-provider.tsx); wiped on logout.
export const QUERY_CACHE_KEY = "sarj-wiki-query-cache";

/**
 * Validate a redirect path to prevent open redirect attacks.
 * Only allows relative paths that start with a single "/". Backslashes
 * are rejected — URL parsers normalize "\" to "/", turning "/\evil.com"
 * into the protocol-relative "//evil.com".
 */
export function sanitizeRedirectPath(path: string): string {
  if (
    typeof path !== "string" ||
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    path.includes("://")
  ) {
    return "/";
  }
  return path;
}
