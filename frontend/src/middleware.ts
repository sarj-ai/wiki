import { NextResponse, type NextRequest } from "next/server";

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
  matcher: ["/", "/d/:path*"],
};
