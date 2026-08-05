import type { Metadata } from "next";

import { QuickLinksLoader } from "@/components/quick-links/quick-links-loader";

export const metadata: Metadata = { title: "Quick links" };

export default function QuickLinksPage() {
  return <QuickLinksLoader />;
}
