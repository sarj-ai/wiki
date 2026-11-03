import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { demoFileFor, demoHref } from "./components/wiki-demo/demo-data";
import middleware from "./middleware";

const PRIVATE_TITLE = "Confidential Acquisition Plan";
const PUBLIC_DOCUMENT_PATH = publicDocumentPath();

function publicDocumentPath(): string {
  const id = new URL(demoFileFor(null).previewUrl).pathname.split("/", 4)[3];
  if (id === undefined) throw new Error("A public demo preview must identify its file");
  return `/d/${id}`;
}

function preview(path: string, userAgent = "Slackbot-LinkExpanding 1.0") {
  return middleware(
    new NextRequest(`https://wiki.sarj.ai${path}`, {
      headers: { "user-agent": userAgent },
    }),
  );
}

describe("private Wiki link previews", () => {
  it.each(["GET", "HEAD"])("opens only the static public fixture for anonymous %s", (method) => {
    const response = middleware(
      new NextRequest(`https://wiki.sarj.ai${PUBLIC_DOCUMENT_PATH}?redirect=https://example.com`, {
        method,
      }),
    );
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      `https://wiki.sarj.ai${demoHref(demoFileFor(null))}`,
    );
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it.each(["access_token", "refresh_token"])(
    "keeps the real document route for signed-in users with %s",
    (cookie) => {
      const request = new NextRequest(`https://wiki.sarj.ai${PUBLIC_DOCUMENT_PATH}`);
      request.cookies.set(cookie, "authenticated");
      const response = middleware(request);
      expect(response.headers.get("x-middleware-next")).toBe("1");
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("cache-control")).toBe("private, no-store");
    },
  );

  it("keeps unauthenticated writes behind login even for public sample IDs", () => {
    const response = middleware(
      new NextRequest(`https://wiki.sarj.ai${PUBLIC_DOCUMENT_PATH}`, { method: "POST" }),
    );
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toContain("/login?redirect=");
  });

  it("returns the same static, generic card for every document path", async () => {
    const known = preview(`/d/${encodeURIComponent(PRIVATE_TITLE)}`);
    const unknown = preview("/d/unknown-document");
    const [knownHtml, unknownHtml] = await Promise.all([known.text(), unknown.text()]);

    expect(known.status).toBe(200);
    expect(known.headers.get("cache-control")).toBe("private, no-store");
    expect(known.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(knownHtml).toBe(unknownHtml);
    expect(knownHtml).toContain('property="og:title" content="Sarj Wiki"');
    expect(knownHtml).toContain('name="twitter:card" content="summary"');
    expect(knownHtml).not.toContain(PRIVATE_TITLE);
    expect(knownHtml).not.toContain("unknown-document");
    expect(knownHtml).not.toContain("<script");
  });

  it("does not let ordinary unauthenticated clients bypass login", () => {
    const response = preview("/d/private", "Mozilla/5.0");
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "https://wiki.sarj.ai/login?redirect=%2Fd%2Fprivate",
    );
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it("prevents browser caching of authenticated document and RSC responses", () => {
    const request = new NextRequest("https://wiki.sarj.ai/d/private");
    request.cookies.set("access_token", "authenticated");
    const response = middleware(request);

    expect(response.headers.get("x-middleware-next")).toBe("1");
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it("preserves a short link through login", () => {
    const response = preview("/go/eng-onboarding", "Mozilla/5.0");
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "https://wiki.sarj.ai/login?redirect=%2Fgo%2Feng-onboarding",
    );
  });

  it("protects the quick-links index and preserves it through login", () => {
    const response = preview("/go", "Mozilla/5.0");
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://wiki.sarj.ai/login?redirect=%2Fgo");
  });
});
