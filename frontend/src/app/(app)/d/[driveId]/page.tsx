import type { Metadata } from "next";

import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { getPublicDocName } from "@/lib/public-api";
import { serverWikiApi } from "@/lib/server-api";

function docMetadata(name: string): Metadata {
  return { openGraph: { siteName: "Sarj Wiki", title: name }, title: name };
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ driveId: string }>;
}): Promise<Metadata> {
  const { driveId } = await params;
  try {
    const file = await serverWikiApi.getFile(driveId);
    return docMetadata(file.name);
  } catch {
    // getFile throws on 401/404. No session usually means a link-preview
    // crawler — fall back to the public cache-backed name so unfurls can
    // show the doc title; if that misses too, the root default title wins.
    const name = await getPublicDocName(driveId);
    return name === null ? {} : docMetadata(name);
  }
}

export default async function FilePage({ params }: { params: Promise<{ driveId: string }> }) {
  const { driveId } = await params;
  return <FilePageClient driveId={driveId} />;
}
