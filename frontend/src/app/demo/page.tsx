import type { Metadata } from "next";
import { Suspense } from "react";

import { DemoWorkspace } from "@/components/wiki-demo/demo-workspace";
import { DocumentLoading } from "@/components/wiki-document/document-loading";

const DEMO_TITLE = "Try Wiki · Public demo";
const DEMO_DESCRIPTION =
  "Explore a read-only Wiki workspace with real document, presentation, spreadsheet, and HTML samples. Compare a messy draft with its cleanly formatted version. No sign-in required.";

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
