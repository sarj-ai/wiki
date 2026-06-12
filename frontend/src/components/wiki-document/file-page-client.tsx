"use client";

import { useQueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { useEffect, useState } from "react";

import type { BreadcrumbItem } from "@/types/wiki";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { FilePageSkeleton } from "@/components/wiki-document/file-page-skeleton";
import { WikiToolbar } from "@/components/wiki-document/wiki-toolbar";
import { WikiNotFound } from "@/components/wiki-not-found";
import { useFile } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/wiki-api";
import { findCachedNodeMeta } from "@/lib/wiki-cache";

export function FilePageClient({ driveId }: { driveId: string }) {
  const queryClient = useQueryClient();
  const fileQuery = useFile(driveId);
  // Counter the toolbar's Reload button bumps to force the embedded
  // iframe to remount with a clean Google editor session — recovery
  // path for blank/black frames after long browser-tab backgrounding.
  const [reloadKey, setReloadKey] = useState(0);

  // Treat "still trying" as loading even after the first attempt
  // errored. React Query's ``retry: 1`` policy and ky's 401 → refresh
  // → replay both produce a window where ``isError`` is true while a
  // recovery attempt is in flight (``isFetching: true`` with no data
  // yet). Without this guard the page flashes the "can't load" UI on
  // hard refresh whenever the access token has expired, then snaps to
  // the file once the retry succeeds.
  const isLoading = fileQuery.isPending || (!fileQuery.data && fileQuery.isFetching);
  if (isLoading) {
    const hint = findCachedNodeMeta(queryClient, driveId);
    if (!hint) {
      return <FilePageSkeleton />;
    }
    return (
      <FilePageBody
        fileId={driveId}
        mimeType={hint.mimeType}
        title={hint.name}
        breadcrumb={[]}
        reloadKey={reloadKey}
        onReload={() => {
          setReloadKey((n) => n + 1);
        }}
      />
    );
  }

  // 404 from the backend means either the file truly doesn't exist or
  // it's outside Sarj Wiki's strict shared-drive scope (My Drive
  // files, non-member shared drives).
  if (fileQuery.isError && fileQuery.error instanceof ApiError && fileQuery.error.status === 404) {
    notFound();
  }

  // After the isLoading guard above, ``isError`` is the only way we
  // can have no usable data — React Query's success branch always
  // populates ``data``, so the type narrows to non-null below.
  if (fileQuery.isError) {
    return (
      <WikiNotFound
        title="Can't load this file"
        description="Something went wrong fetching this file from Google Drive. Try again in a moment, or pick another file from the sidebar."
      />
    );
  }

  const file = fileQuery.data;

  return (
    <FilePageBody
      fileId={file.id}
      mimeType={file.mimeType}
      title={file.name}
      breadcrumb={file.breadcrumb}
      reloadKey={reloadKey}
      onReload={() => {
        setReloadKey((n) => n + 1);
      }}
    />
  );
}

interface FilePageBodyProps {
  fileId: string;
  mimeType: string;
  title: string;
  breadcrumb: BreadcrumbItem[];
  reloadKey: number;
  onReload: () => void;
}

function FilePageBody({
  fileId,
  mimeType,
  title,
  breadcrumb,
  reloadKey,
  onReload,
}: FilePageBodyProps) {
  const setActive = useDocViewerStore((s) => s.setActive);
  const setSlot = useDocViewerStore((s) => s.setSlot);

  useEffect(() => {
    setActive({ fileId, mimeType, title, reloadKey });
  }, [fileId, mimeType, title, reloadKey, setActive]);
  useEffect(
    () => () => {
      setActive(null);
    },
    [setActive],
  );

  return (
    <div className="flex h-[100dvh] flex-col">
      <WikiToolbar
        fileId={fileId}
        mimeType={mimeType}
        title={title}
        breadcrumb={breadcrumb}
        onReload={onReload}
      />
      <div ref={setSlot} className="flex-1 overflow-hidden" />
    </div>
  );
}
