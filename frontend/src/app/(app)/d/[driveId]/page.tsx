import type { Metadata } from "next";

import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { serverWikiApi } from "@/lib/server-api";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ driveId: string }>;
}): Promise<Metadata> {
  const { driveId } = await params;
  try {
    const file = await serverWikiApi.getFile(driveId);
    return { title: file.name };
  } catch {
    // getFile throws on 401/404 — fall back to the root layout's default title.
    return {};
  }
}

export default async function FilePage({ params }: { params: Promise<{ driveId: string }> }) {
  const { driveId } = await params;
  return <FilePageClient driveId={driveId} />;
}
