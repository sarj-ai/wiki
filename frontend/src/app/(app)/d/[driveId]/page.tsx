import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";

import { FilePageClient } from "@/components/wiki-document/file-page-client";
import { zFileDetail } from "@/lib/generated/zod.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { wikiKeys } from "@/lib/wiki-keys";

export default async function FilePage({ params }: { params: Promise<{ driveId: string }> }) {
  const { driveId } = await params;
  const queryClient = new QueryClient();
  try {
    await queryClient.fetchQuery({
      queryKey: wikiKeys.file(driveId),
      queryFn: () => serverGet(`files/${encodeURIComponent(driveId)}`, zFileDetail),
    });
  } catch (err) {
    // 404 → (app)/not-found in the first response; anything else is
    // swallowed and the client skeleton/error flow takes over.
    if (err instanceof ServerApiError && err.status === 404) {
      notFound();
    }
  }
  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <FilePageClient driveId={driveId} />
    </HydrationBoundary>
  );
}
