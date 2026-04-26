"use client";

import { notFound, useParams } from "next/navigation";
import { ApiError } from "@/lib/navigator-api";
import { useFile } from "@/hooks/use-navigator-tree";
import { FileViewer } from "@/components/navigator-document/file-viewer";
import { NavigatorToolbar } from "@/components/navigator-document/navigator-toolbar";
import { NavigatorNotFound } from "@/components/navigator-not-found";

export default function FilePage() {
  const params = useParams<{ driveId: string }>();
  const fileId = params.driveId ?? null;
  const fileQuery = useFile(fileId);

  if (fileQuery.isLoading) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        Loading file…
      </div>
    );
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

  if (fileQuery.isError || !fileQuery.data) {
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
