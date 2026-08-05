import type { Metadata } from "next";
import { cookies } from "next/headers";

import { QuickLinks } from "@/components/quick-links/quick-links";
import { getRecentDocumentAliases } from "@/lib/alias-client";

export const metadata: Metadata = { title: "Quick links" };

export default async function QuickLinksPage() {
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) return <QuickLinks aliases={[]} error />;

  const result = await getRecentDocumentAliases(token).catch((error: unknown) => {
    console.error("Recent quick links preload failed", error);
    return null;
  });
  if (result?.status !== "found") return <QuickLinks aliases={[]} error />;
  return <QuickLinks aliases={result.aliases} />;
}
