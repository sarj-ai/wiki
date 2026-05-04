"use client";

import { notFound, useParams } from "next/navigation";

import { FilePageSkeleton } from "@/components/navigator-document/file-page-skeleton";
import { FileViewer } from "@/components/navigator-document/file-viewer";
import { NavigatorToolbar } from "@/components/navigator-document/navigator-toolbar";
import { NavigatorNotFound } from "@/components/navigator-not-found";
import { useFile } from "@/hooks/use-navigator-tree";
import { ApiError } from "@/lib/navigator-api";

export default function FilePage() {
  const params = useParams<{ driveId: string }>();
  const fileQuery = useFile(params.driveId);

  // Treat "still trying" as loading even after the first attempt
  // errored. React Query's ``retry: 1`` policy and ky's 401 → refresh
  // → replay both produce a window where ``isError`` is true while a
  // recovery attempt is in flight (``isFetching: true`` with no data
  // yet). Without this guard the page flashes the "can't load" UI on
  // hard refresh whenever the access token has expired, then snaps to
  // the file once the retry succeeds.
  const isLoading = fileQuery.isPending || (!fileQuery.data && fileQuery.isFetching);
  if (isLoading) {
    return <FilePageSkeleton />;
  }

  // 404 from the backend means either the file truly doesn't exist or
  // it's outside the Navigator's strict shared-drive scope (My Drive
  // files, non-member shared drives). Route through Next's notFound()
  // so the (app)/not-found.tsx boundary renders — that keeps the
  // sidebar mounted and the themed empty-state view consistent with
  // unmatched routes elsewhere in the app.
  if (fileQuery.isError && fileQuery.error instanceof ApiError && fileQuery.error.status === 404) {
    notFound();
  }

  // After the isLoading guard above, ``isError`` is the only way we
  // can have no usable data — React Query's success branch always
  // populates ``data``, so the type narrows to non-null below.
  if (fileQuery.isError) {
    return (
      <NavigatorNotFound
        title="Can't load this file"
        description="Something went wrong fetching this file from Google Drive. Try again in a moment, or pick another file from the sidebar."
      />
    );
  }

  const file = fileQuery.data;

  return (
    <div className="flex h-[100dvh] flex-col">
      <NavigatorToolbar
        fileId={file.id}
        mimeType={file.mimeType}
        title={file.name}
        breadcrumb={file.breadcrumb}
      />
      <div className="flex-1 overflow-hidden">
        <FileViewer fileId={file.id} mimeType={file.mimeType} title={file.name} />
      </div>
    </div>
  );
}
