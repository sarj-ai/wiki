import { FilePageClient } from "@/components/wiki-document/file-page-client";

// Deliberately no server-side ``files/{id}`` prefetch: it blocked the
// RSC response on a Worker → backend → Google round-trip (~1.3s
// measured in production) before the client could even start mounting
// the Google Docs iframe. The client renders instantly from the
// sidebar's cached node metadata (see FilePageClient) while the full
// detail — breadcrumb included — loads in the background; true 404s
// surface through the client query's notFound() branch.
export default async function FilePage({ params }: { params: Promise<{ driveId: string }> }) {
  const { driveId } = await params;
  return <FilePageClient driveId={driveId} />;
}
