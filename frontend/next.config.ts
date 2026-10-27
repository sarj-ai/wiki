import type { NextConfig } from "next";

// Permit the Google-owned frames required by the embedded sharing dialog.
const GOOGLE_FRAME_SRC = [
  "https://*.google.com",
  "https://*.gstatic.com",
  "https://*.googleusercontent.com",
].join(" ");

// Permit the Google API connections required by the embedded sharing dialog.
const GOOGLE_CONNECT_SRC = [
  "https://*.google.com",
  "https://*.googleapis.com",
  "https://*.googleusercontent.com",
  "https://*.gstatic.com",
].join(" ");

// Include the separately hosted FastAPI backend in CSP connections.
const BACKEND_API_ORIGIN = (() => {
  const raw = process.env["NEXT_PUBLIC_API_URL"] ?? "http://localhost:8000";
  try {
    return new URL(raw).origin;
  } catch {
    // Keep local startup available while CI reports the invalid configuration.
    return "http://localhost:8000";
  }
})();

// Allow eval only for development tooling while production retains a strict script policy.
const IS_DEV = process.env.NODE_ENV !== "production";
const SCRIPT_SRC = IS_DEV
  ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
  : "script-src 'self' 'unsafe-inline'";
const CSP_DIRECTIVES = [
  "default-src 'self'",
  SCRIPT_SRC,
  // Tailwind's injected styles require 'unsafe-inline' on style-src.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://*.google.com https://*.gstatic.com https://*.googleusercontent.com",
  "font-src 'self' data: https://*.gstatic.com",
  `connect-src 'self' ${BACKEND_API_ORIGIN} ${GOOGLE_CONNECT_SRC}`,
  `frame-src 'self' ${BACKEND_API_ORIGIN} ${GOOGLE_FRAME_SRC}`,
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

const NEXT_CONFIG: NextConfig = {
  experimental: {
    optimizePackageImports: [
      "lucide-react",
      "radix-ui",
      "@dnd-kit/core",
      "@dnd-kit/sortable",
      "@dnd-kit/utilities",
    ],
  },
  headers: () =>
    Promise.resolve([
      {
        headers: [
          { key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" },
          ...SECURITY_HEADERS,
        ],
        // Static brand assets are immutable in practice; cache them instead of revalidating.
        source: "/brand/:path*",
      },
      ...["/brand/sarj-loader-12132f41.svg", "/brand/sarj-loader-still-12132f41.svg"].map(
        (source) => ({
          headers: [
            { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
            ...SECURITY_HEADERS,
          ],
          source,
        }),
      ),
      {
        headers: [
          { key: "Cache-Control", value: "no-cache, must-revalidate" },
          ...SECURITY_HEADERS,
        ],
        // HTML pages: always revalidate (browser checks for new version).
        source: "/((?!_next/static|_next/image|brand/).*)",
      },
      {
        headers: [{ key: "Cache-Control", value: "private, no-store" }, ...SECURITY_HEADERS],
        source: "/health",
      },
      {
        // Keep private API reads and expired-session errors out of all caches.
        // Cloudflare applies configured headers after route response headers.
        headers: [{ key: "Cache-Control", value: "private, no-store" }],
        source: "/api/:path*",
      },
      {
        headers: [
          ...SECURITY_HEADERS.filter(
            (header) =>
              !["Content-Security-Policy", "Referrer-Policy", "X-Frame-Options"].includes(
                header.key,
              ),
          ),
          { key: "Cache-Control", value: "private, no-store" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Content-Security-Policy",
            value: CSP_DIRECTIVES.replace("frame-ancestors 'self'", "frame-ancestors 'none'"),
          },
        ],
        source: "/mcp/:path*",
      },
    ]),
  output: "standalone",
  reactCompiler: true,
  typedRoutes: true,
};

export default NEXT_CONFIG;
