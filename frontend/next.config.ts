import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  headers: async () => [
    {
      // HTML pages: always revalidate (browser checks for new version)
      source: "/((?!_next/static|_next/image).*)",
      headers: [
        { key: "Cache-Control", value: "no-cache, must-revalidate" },
      ],
    },
  ],
};

export default nextConfig;
