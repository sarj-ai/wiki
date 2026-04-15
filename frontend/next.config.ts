import type { NextConfig } from "next";

// Allowed Google domains for iframe embedding (see PIVOT_PLAN.md §8.1).
// CSP frame-src must permit every origin the Navigator embeds.
const GOOGLE_FRAME_SRC = [
  "https://docs.google.com",
  "https://drive.google.com",
  "https://*.googleusercontent.com",
].join(" ");

// The Navigator frontend always runs at a different origin from the
// FastAPI backend (localhost:3000 ↔ localhost:8000 in dev, and behind
// different subdomains in prod). CSP connect-src must list the backend
// explicitly or every fetch() from the client is silently blocked.
const BACKEND_API_ORIGIN = (() => {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    return new URL(raw).origin;
  } catch {
    // Misconfigured env — fall back to the dev default so the app still
    // boots locally. CI picks this up and flags the misconfig separately.
    return "http://localhost:8000";
  }
})();

// Content-Security-Policy directives for the Navigator.
// 'unsafe-inline' on style-src is required for Tailwind's injected styles.
// 'unsafe-eval' is NOT included — if Next.js dev mode complains, narrow the
// directive further rather than opening it up.
const CSP_DIRECTIVES = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://lh3.googleusercontent.com https://*.googleusercontent.com",
  "font-src 'self' data:",
  `connect-src 'self' ${BACKEND_API_ORIGIN} https://accounts.google.com https://oauth2.googleapis.com https://www.googleapis.com`,
  `frame-src 'self' ${GOOGLE_FRAME_SRC}`,
  "frame-ancestors 'self'",
  "base-uri 'self'",
  "form-action 'self' https://accounts.google.com",
  "object-src 'none'",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP_DIRECTIVES },
  {
    key: "Strict-Transport-Security",
    value: "max-age=31536000; includeSubDomains; preload",
  },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  headers: async () => [
    {
      // HTML pages: always revalidate (browser checks for new version).
      source: "/((?!_next/static|_next/image).*)",
      headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }, ...SECURITY_HEADERS],
    },
  ],
};

export default nextConfig;
