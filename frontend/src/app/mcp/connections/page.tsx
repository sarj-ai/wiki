import type { Metadata } from "next";

import { McpConnections } from "@/components/mcp/mcp-connections";
import { SarjLogo } from "@/components/sarj-logo";

export const metadata: Metadata = {
  referrer: "no-referrer",
  robots: { follow: false, index: false },
  title: "App connections · Wiki",
};

export default function ConnectionsPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-8 px-4 py-10">
      <SarjLogo className="text-primary" size={40} />
      <McpConnections />
    </main>
  );
}
