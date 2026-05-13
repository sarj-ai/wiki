import type { NextConfig } from "next";

// Every Google-owned origin Sarj Wiki needs to iframe or talk to.
// Google Docs' Share dialog loads nested people-picker frames from
// several of these hosts (contacts.google.com, apis.google.com,
// accounts.google.com), so the embedded /edit URL silently fails when
// frame-src is locked down to docs/drive only — the top frame loads
// but the share dialog's inner frame gets CSP-blocked with no visible
// error, and the button click appears to do nothing.
const GOOGLE_FRAME_SRC = [
  "https://*.google.com",
  "https://*.gstatic.com",
  "https://*.googleusercontent.com",
].join(" ");

// Same rationale for connect-src: the share dialog makes fetch calls
// to the Google People / Contacts APIs which live on *.googleapis.com
// and clients6.google.com. Without these the dialog's JS runs but
// every API call is blocked by CSP and the UI does nothing.
const GOOGLE_CONNECT_SRC = [
  "https://*.google.com",
  "https://*.googleapis.com",
  "https://*.googleusercontent.com",
  "https://*.gstatic.com",
].join(" ");

// The Sarj Wiki frontend always runs at a different origin from the
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

// Content-Security-Policy directives for Sarj Wiki.
// 'unsafe-inline' on style-src is required for Tailwind's injected styles.
// 'unsafe-eval' is ONLY added in development — Next.js Turbopack + React
// dev mode use eval() for HMR and callstack reconstruction. Production
// builds keep the strict 'script-src' with no eval.
const IS_DEV = process.env.NODE_ENV !== "production";
const SCRIPT_SRC = IS_DEV
  ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
  : "script-src 'self' 'unsafe-inline'";
const CSP_DIRECTIVES = [
  "default-src 'self'",
  SCRIPT_SRC,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://*.google.com https://*.gstatic.com https://*.googleusercontent.com",
  "font-src 'self' data: https://*.gstatic.com",
  `connect-src 'self' ${BACKEND_API_ORIGIN} ${GOOGLE_CONNECT_SRC}`,
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
  reactCompiler: true,
  headers: async () => [
    {
      // HTML pages: always revalidate (browser checks for new version).
      source: "/((?!_next/static|_next/image).*)",
      headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }, ...SECURITY_HEADERS],
    },
  ],
};

export default nextConfig;
