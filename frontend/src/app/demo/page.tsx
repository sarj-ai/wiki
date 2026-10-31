import type { Metadata } from "next";
import { Suspense } from "react";

import { DemoWorkspace } from "@/components/wiki-demo/demo-workspace";
import { DocumentLoading } from "@/components/wiki-document/document-loading";

const DEMO_TITLE = "Nightingale · Wiki public demo";
const DEMO_DESCRIPTION =
  "Read the nightingale article, compare recorded processing results, and explore native Google Docs, Sheets, Slides and Office downloads in a read-only Wiki workspace.";

export const metadata: Metadata = {
  alternates: { canonical: "https://wiki.sarj.ai/demo" },
  description: DEMO_DESCRIPTION,
  openGraph: {
    description: DEMO_DESCRIPTION,
    siteName: "Sarj Wiki",
    title: DEMO_TITLE,
    type: "website",
    url: "https://wiki.sarj.ai/demo",
  },
  title: DEMO_TITLE,
  twitter: { card: "summary", description: DEMO_DESCRIPTION, title: DEMO_TITLE },
};

export default function DemoPage() {
  return (
    <Suspense fallback={<DocumentLoading label="Opening the demo…" />}>
      <DemoWorkspace />
    </Suspense>
  );
}
