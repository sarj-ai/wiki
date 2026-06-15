"use client";

import { useQueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import type { BreadcrumbItem } from "@/types/wiki";

import { Skeleton } from "@/components/ui/skeleton";
import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { WikiToolbar } from "@/components/wiki-document/wiki-toolbar";
import { WikiNotFound } from "@/components/wiki-not-found";
import { useFile } from "@/hooks/use-wiki-tree";
import { saveLastDoc } from "@/lib/last-doc";
import { markNav, markPaint } from "@/lib/perf-marks";
import { ApiError } from "@/lib/wiki-api";
import { findCachedNodeMeta } from "@/lib/wiki-cache";

export function FilePageClient({ driveId }: { driveId: string }) {
  const queryClient = useQueryClient();
  const fileQuery = useFile(driveId);

  const [reloadKey, setReloadKey] = useState(0);

  const committed = useRef<string | null>(null);
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

  if (fileQuery.isError && fileQuery.error instanceof ApiError && fileQuery.error.status === 404) {
    notFound();
  }

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
    markPaint(fileId, "toolbarPaint");
  }, [fileId]);
  useEffect(() => {
    setActive({ fileId, mimeType, title, reloadKey });
    saveLastDoc({ fileId, mimeType, title });
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

function FilePagePending() {
  return (
    <div className="flex h-[100dvh] flex-col" aria-busy="true" aria-label="Loading file">
      <header className="border-border bg-background flex items-center gap-2 border-b px-4 py-2">
        <Skeleton className="size-4 shrink-0 rounded" />
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
