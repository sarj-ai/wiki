import type { Metadata } from "next";

import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { displayFileName } from "@/lib/file-icons";
import { serverWikiApi } from "@/lib/server-api";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ driveId: string }>;
}): Promise<Metadata> {
  const { driveId } = await params;
  try {
    const file = await serverWikiApi.getFile(driveId);
    return docMetadata(displayFileName(file.name, file.mimeType));
  } catch (error) {
    // Keep generic metadata when authenticated title lookup fails.
    console.error("Authenticated document metadata lookup failed", error);
    return {};
  }
}

export default async function FilePage({ params }: { params: Promise<{ driveId: string }> }) {
  const { driveId } = await params;
  return <FilePageClient driveId={driveId} />;
}

function docMetadata(name: string): Metadata {
  return { openGraph: { siteName: "Sarj Wiki", title: name }, title: name };
}
