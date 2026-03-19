/**
 * Validate a redirect path to prevent open redirect attacks.
 * Only allows relative paths that start with a single "/".
 */
export function sanitizeRedirectPath(path: string): string {
  if (
    typeof path !== "string" ||
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("://")
  ) {
    return "/";
  }
  return path;
}
