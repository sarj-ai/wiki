import { NextResponse, type NextRequest } from "next/server";

// UX gate only — the backend verifies the JWT on every API call.
// Either cookie passes: access_token lives 30 min, refresh_token 7 d,
// and the client silently refreshes on 401. /login is not inverse-
// gated: a revoked-but-present refresh token would livelock between
// this gate and the client's 401 redirect. middleware.ts, not
// proxy.ts: @opennextjs/cloudflare rejects Node middleware and
// proxy.ts is Node-only in Next 16.
export default function middleware(request: NextRequest) {
  if (request.cookies.has("access_token") || request.cookies.has("refresh_token")) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  const redirect = url.pathname + url.search;
  url.pathname = "/login";
  url.search = "";
  if (redirect !== "/") {
    url.searchParams.set("redirect", redirect);
  }
  return NextResponse.redirect(url);
}

export const config = {
  // Explicit allowlist — the app has exactly two protected pages.
  matcher: ["/", "/d/:path*"],
};
