import { NextResponse, type NextRequest } from "next/server";

// Link-preview crawlers (Slack, Discord, Teams, iMessage, …) must reach the
// page to read its og: tags. They see server-rendered metadata and the
// login-gated shell — never document content. The user-agent check is
// spoofable by design: the only thing it exposes is the doc title, which
// /link-meta already serves publicly as a deliberate product decision.
const UNFURL_CRAWLERS =
  /slackbot|discordbot|twitterbot|facebookexternalhit|telegrambot|whatsapp|linkedinbot|skypeuripreview|google-pagerenderer/i;

export default function middleware(request: NextRequest) {
  if (request.cookies.has("access_token") || request.cookies.has("refresh_token")) {
    return NextResponse.next();
  }
  if (UNFURL_CRAWLERS.test(request.headers.get("user-agent") ?? "")) {
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
