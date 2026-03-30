import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Server-side route protection.
 *
 * Checks for the presence of auth cookies before rendering protected pages.
 * This prevents the "dashboard flash" where unauthenticated users briefly see
 * the app UI before the client-side AuthProvider redirects them.
 *
 * NOTE: This only checks cookie *presence*, not validity. The AuthProvider
 * still handles full token validation + refresh on the client side.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const hasAccess = request.cookies.has("access_token");
  const hasRefresh = request.cookies.has("refresh_token");
  const isAuthenticated = hasAccess || hasRefresh;

  // Unauthenticated user hitting a protected route → redirect to login
  // Skip if on the root path — the AuthProvider handles refresh there,
  // and the user may have just completed OAuth (cookies in-flight).
  if (!isAuthenticated && pathname !== "/") {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("redirect", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!login|_next/static|_next/image|favicon\\.ico|api).*)",
  ],
};
