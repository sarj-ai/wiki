import type { Metadata } from "next";

import { QuickLinkVisitRecorder } from "@/components/quick-links/quick-link-visit-recorder";
import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { displayFileName } from "@/lib/file-icons";
import { zDocumentAliasWrite } from "@/lib/generated/zod.gen";
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

export default async function FilePage({
  params,
  searchParams,
}: {
  params: Promise<{ driveId: string }>;
  searchParams: Promise<{ go?: string | string[] }>;
}) {
  const { driveId } = await params;
  const { go } = await searchParams;
  const parsed = zDocumentAliasWrite.safeParse({ alias: typeof go === "string" ? go : "" });
  return (
    <>
      {parsed.success ? <QuickLinkVisitRecorder alias={parsed.data.alias} /> : null}
      <FilePageClient driveId={driveId} />
    </>
  );
}

function docMetadata(name: string): Metadata {
  return { openGraph: { siteName: "Sarj Wiki", title: name }, title: name };
}
