import type { Metadata } from "next";
import { Suspense } from "react";

import { DemoWorkspace } from "@/components/wiki-demo/demo-workspace";
import { DocumentLoading } from "@/components/wiki-document/document-loading";

const DEMO_TITLE = "Sarj Wiki · Public demo";
const DEMO_DESCRIPTION =
  "Explore a Notion-like interface for Google Workspace. Browse an overview, a spreadsheet and a presentation in a read-only Sarj Wiki demo.";

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
  twitter: {
    card: "summary",
    description: DEMO_DESCRIPTION,
    title: DEMO_TITLE,
  },
};

export default function DemoPage() {
  return (
    <Suspense fallback={<DocumentLoading label="Opening the demo…" />}>
      <DemoWorkspace />
    </Suspense>
  );
}
