import type { Metadata } from "next";
import Link from "next/link";

import { McpConnections } from "@/components/mcp/mcp-connections";
import { SarjLogo } from "@/components/sarj-logo";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  referrer: "no-referrer",
  robots: { follow: false, index: false },
  title: "App connections",
};

export default function ConnectionsPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-8 px-4 py-10">
      <Button asChild className="text-muted-foreground px-0" variant="link">
        <Link href="/">Back to Wiki</Link>
      </Button>
      <SarjLogo className="text-primary" size={40} />
      <McpConnections />
    </main>
  );
}
