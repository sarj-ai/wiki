import { FilePageClient } from "@/components/wiki-document/file-page-client";

export default async function FilePage({ params }: { params: Promise<{ driveId: string }> }) {
  const { driveId } = await params;
  return <FilePageClient driveId={driveId} />;
}
