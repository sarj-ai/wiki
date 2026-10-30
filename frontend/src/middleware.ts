import { type NextRequest, NextResponse } from "next/server";

// Return only static content to spoofable link-preview user agents.
const UNFURL_CRAWLERS =
  /slackbot|discordbot|twitterbot|facebookexternalhit|telegrambot|whatsapp|linkedinbot|skypeuripreview|google-pagerenderer/i;

const PREVIEW_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Sarj Wiki">
<meta property="og:title" content="Sarj Wiki">
<meta property="og:description" content="Internal knowledge base for Sarj">
<meta property="og:image" content="https://wiki.sarj.ai/icon.png">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Sarj Wiki">
<meta name="twitter:description" content="Internal knowledge base for Sarj">
<meta name="twitter:image" content="https://wiki.sarj.ai/icon.png">
<title>Sarj Wiki</title>
</head>
<body>Sarj Wiki</body>
</html>`;

export default function middleware(request: NextRequest) {
  if (request.cookies.has("access_token") || request.cookies.has("refresh_token")) {
    const response = NextResponse.next();
    response.headers.set("cache-control", "private, no-store");
    return response;
  }
  if (UNFURL_CRAWLERS.test(request.headers.get("user-agent") ?? "")) {
    return new NextResponse(PREVIEW_HTML, {
      headers: {
        "cache-control": "private, no-store",
        "content-security-policy": "default-src 'none'; img-src https://wiki.sarj.ai",
        "content-type": "text/html; charset=utf-8",
        "x-content-type-options": "nosniff",
      },
      status: 200,
    });
  }
  const url = request.nextUrl.clone();
  const redirect = url.pathname + url.search;
  url.pathname = "/login";
  url.search = "";
  if (redirect !== "/") url.searchParams.set("redirect", redirect);
  const response = NextResponse.redirect(url);
  response.headers.set("cache-control", "private, no-store");
  return response;
}

export const config = {
  matcher: ["/", "/d/:path*", "/go/:path*"],
} as const;
