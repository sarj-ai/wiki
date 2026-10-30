import type { Metadata } from "next";
import Link from "next/link";

import { McpConsentLoader } from "@/components/mcp/mcp-consent-loader";
import { SarjLogo } from "@/components/sarj-logo";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  referrer: "no-referrer",
  robots: { follow: false, index: false },
  title: "Review app access",
};

export default async function ConsentPage({
  searchParams,
}: {
  searchParams: Promise<{ intent?: string | string[] }>;
}) {
  const { intent } = await searchParams;
  return (
    <main className="mx-auto flex min-h-dvh max-w-2xl flex-col items-center justify-center gap-8 px-4 py-10">
      <Button asChild className="text-muted-foreground self-start px-0" variant="link">
        <Link href="/">Back to Wiki</Link>
      </Button>
      <SarjLogo className="text-primary" size={40} />
      {typeof intent === "string" && /^wm_intent_[A-Za-z0-9_-]{43}$/.test(intent) ? (
        <McpConsentLoader intent={intent} key={intent} />
      ) : (
        <section className="bg-card w-full space-y-3 rounded-xl border p-6">
          <h1 className="text-xl font-semibold">Connection request unavailable</h1>
          <p className="text-muted-foreground text-sm" role="alert">
            Invalid connection request. Start a new connection from your app.
          </p>
        </section>
      )}
    </main>
  );
}
