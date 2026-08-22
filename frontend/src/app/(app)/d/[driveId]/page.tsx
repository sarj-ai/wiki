import { QuickLinkVisitRecorder } from "@/components/quick-links/quick-link-visit-recorder";
import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { zDocumentAliasWrite } from "@/lib/generated/zod.gen";

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
