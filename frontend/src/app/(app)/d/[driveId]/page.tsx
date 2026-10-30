import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { headers } from "next/headers";

import { QuickLinkVisitRecorder } from "@/components/quick-links/quick-link-visit-recorder";
import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { zDocumentAliasWrite } from "@/lib/generated/zod.gen";
import { serverWikiApi } from "@/lib/server-api";
import { wikiKeys } from "@/lib/wiki-keys";

const DIRECT_FILE_PREFETCH_TIMEOUT_MS = 1_500;

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
  const queryClient = new QueryClient();
  const requestHeaders = await headers();
  const isClientNavigation = requestHeaders.get("rsc") === "1";
  // Deep links avoid a browser metadata waterfall. Client navigations already
  // prefetch into the shared QueryClient and must not be gated by a duplicate
  // server/provider request.
  const file = isClientNavigation
    ? null
    : await serverWikiApi
        .getFile(driveId, AbortSignal.timeout(DIRECT_FILE_PREFETCH_TIMEOUT_MS))
        .catch(() => null);
  if (file) queryClient.setQueryData(wikiKeys.file(driveId), file);
  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      {parsed.success ? <QuickLinkVisitRecorder alias={parsed.data.alias} /> : null}
      <FilePageClient driveId={driveId} />
    </HydrationBoundary>
  );
}
