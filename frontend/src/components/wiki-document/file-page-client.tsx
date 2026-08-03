"use client";

import { useQueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { WikiToolbar } from "@/components/wiki-document/wiki-toolbar";
import { WikiNotFound } from "@/components/wiki-not-found";
import { useFile } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/api-error";
import { displayFileName } from "@/lib/file-icons";
import { saveLastDoc } from "@/lib/last-doc";
import { markNav, markPaint } from "@/lib/perf-marks";
import { findCachedNodeMeta } from "@/lib/wiki-cache";
import { type BreadcrumbItem, MIME_HTML } from "@/types/wiki";

export function FilePageClient({ driveId }: { driveId: string }) {
  const queryClient = useQueryClient();
  const fileQuery = useFile(driveId);
  const htmlMimeType = fileQuery.data?.mimeType;
  const refetchFile = fileQuery.refetch;

  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (htmlMimeType !== MIME_HTML) return;
    const interval = window.setInterval(
      () => {
        void refetchFile();
      },
      5 * 60 * 1000,
    );
    return () => {
      window.clearInterval(interval);
    };
  }, [htmlMimeType, refetchFile]);

  const committed = useRef<null | string>(null);
  useEffect(() => {
    if (committed.current === driveId) return;
    committed.current = driveId;
    markNav(driveId, "commit");
  }, [driveId]);

  const isLoading = fileQuery.isPending || (!fileQuery.data && fileQuery.isFetching);
  if (isLoading) {
    const hint = findCachedNodeMeta(queryClient, driveId);
    if (!hint) {
      return <FilePagePending />;
    }
    return (
      <FilePageBody
        breadcrumb={[]}
        canEdit={false}
        fileId={driveId}
        mimeType={hint.mimeType}
        onReload={() => {
          setReloadKey((n) => n + 1);
        }}
        reloadKey={reloadKey}
        title={hint.name}
      />
    );
  }

  if (fileQuery.isError && fileQuery.error instanceof ApiError && fileQuery.error.status === 404) {
    notFound();
  }

  if (fileQuery.isError) {
    return (
      <WikiNotFound
        description="Something went wrong fetching this file from Google Drive. Try again in a moment, or pick another file from the sidebar."
        title="Can't load this file"
      />
    );
  }

  const file = fileQuery.data;

  return (
    <FilePageBody
      breadcrumb={file.breadcrumb}
      canEdit={file.canEdit}
      fileId={file.id}
      mimeType={file.mimeType}
      onReload={() => {
        setReloadKey((n) => n + 1);
      }}
      reloadKey={reloadKey}
      title={file.name}
      {...(file.modifiedTime === undefined ? {} : { version: file.modifiedTime })}
    />
  );
}

interface FilePageBodyProps {
  breadcrumb: BreadcrumbItem[];
  canEdit: boolean;
  fileId: string;
  mimeType: string;
  onReload: () => void;
  reloadKey: number;
  title: string;
  version?: string;
}

function FilePageBody({
  breadcrumb,
  canEdit,
  fileId,
  mimeType,
  onReload,
  reloadKey,
  title,
  version,
}: FilePageBodyProps) {
  const setActive = useDocViewerStore((s) => s.setActive);
  const setSlot = useDocViewerStore((s) => s.setSlot);
  const displayTitle = displayFileName(title, mimeType);

  useEffect(() => {
    markPaint(fileId, "toolbarPaint");
  }, [fileId]);
  useEffect(() => {
    setActive({
      fileId,
      mimeType,
      reloadKey,
      title: displayTitle,
      ...(version === undefined ? {} : { version }),
    });
    saveLastDoc({ fileId, mimeType, title: displayTitle });
  }, [displayTitle, fileId, mimeType, reloadKey, setActive, version]);
  useEffect(
    () => () => {
      setActive(null);
    },
    [setActive],
  );

  return (
    <div className="flex h-[100dvh] flex-col">
      <WikiToolbar
        breadcrumb={breadcrumb}
        canEdit={canEdit}
        fileId={fileId}
        mimeType={mimeType}
        onReload={onReload}
        title={displayTitle}
      />
      <div className="flex-1 overflow-hidden" ref={setSlot} />
    </div>
  );
}

function FilePagePending() {
  return (
    <div aria-busy="true" aria-label="Loading file" className="flex h-[100dvh] flex-col">
      <header className="border-border bg-background flex items-center gap-2 border-b px-4 py-2">
        <Skeleton className="size-4 shrink-0 rounded-sm" />
        <Skeleton className="h-4 w-48" />
        <div className="ml-auto flex items-center gap-2">
          <Skeleton className="h-6 w-8" />
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-28" />
        </div>
      </header>
      <div className="flex-1 overflow-hidden">
        <DocSkeleton />
      </div>
    </div>
  );
}
