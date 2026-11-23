import { type NextRequest, NextResponse } from "next/server";

import { demoHrefForDocumentPath } from "@/components/wiki-demo/demo-file-urls";

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

const RESPONSE_HEADERS = {
  "cache-control": "private, no-store",
  "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
} as const;

export default function middleware(request: NextRequest) {
  if (
    request.cookies.has("access_token") ||
    request.cookies.has("refresh_token")
  ) {
    const response = NextResponse.next();
    response.headers.set("cache-control", "private, no-store");
    return response;
  }
  const demoHref = demoHrefForDocumentPath(request.nextUrl.pathname);
  if (
    demoHref !== null &&
    (request.method === "GET" || request.method === "HEAD")
  ) {
    return redirect(new URL(demoHref, request.url));
  }
  if (UNFURL_CRAWLERS.test(request.headers.get("user-agent") ?? "")) {
    return new NextResponse(PREVIEW_HTML, {
      headers: {
        ...RESPONSE_HEADERS,
        "content-security-policy":
          "default-src 'none'; img-src https://wiki.sarj.ai; frame-ancestors 'none'",
        "content-type": "text/html; charset=utf-8",
      },
      status: 200,
    });
  }
  const url = request.nextUrl.clone();
  const target = url.pathname + url.search;
  url.pathname = "/login";
  url.search = "";
  if (target !== "/") url.searchParams.set("redirect", target);
  return redirect(url);
}

function redirect(url: URL): NextResponse {
  const response = NextResponse.redirect(url);
  for (const [name, value] of Object.entries(RESPONSE_HEADERS))
    response.headers.set(name, value);
  return response;
}

export const config = {
  matcher: ["/", "/d/:path*", "/go/:path*"],
} as const;
