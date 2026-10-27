"use client";

import { useQueryClient } from "@tanstack/react-query";
import { notFound } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { DocumentLoading } from "@/components/wiki-document/document-loading";
import { WikiToolbar } from "@/components/wiki-document/wiki-toolbar";
import { WikiNotFound } from "@/components/wiki-not-found";
import { useDocumentTitle } from "@/hooks/use-document-title";
import { useFile } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/api-error";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { displayFileName } from "@/lib/file-icons";
import { mobileReadUrlFor } from "@/lib/file-urls";
import { saveLastDoc } from "@/lib/last-doc";
import { markNav, markPaint } from "@/lib/perf-marks";
import { findCachedNodeMeta } from "@/lib/wiki-cache";
import { type BreadcrumbItem, MIME_HTML } from "@/types/wiki";

export function FilePageClient({ driveId }: { driveId: string }) {
  const queryClient = useQueryClient();
  const fileQuery = useFile(driveId);
  const htmlMimeType = fileQuery.data?.mimeType;
  const refetchFile = fileQuery.refetch;

  const [reload, setReload] = useState({ fileId: driveId, key: 0 });
  const reloadKey = reload.fileId === driveId ? reload.key : 0;
  const reloadCurrent = () => {
    setReload((current) => ({
      fileId: driveId,
      key: current.fileId === driveId ? current.key + 1 : 1,
    }));
  };

  useEffect(() => {
    if (htmlMimeType !== MIME_HTML) return;
    const interval = window.setInterval(
      () => {
        if (document.visibilityState === "visible") void refetchFile();
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
    if (!hint || (hint.mimeType === MIME_HTML && hint.modifiedTime === undefined)) {
      return <FilePagePending />;
    }
    return (
      <FilePageBody
        authority="pending"
        breadcrumb={[]}
        canEdit={false}
        driveId={undefined}
        fileId={driveId}
        mimeType={hint.mimeType}
        onReload={() => {
          reloadCurrent();
        }}
        parentFolderId={undefined}
        reloadKey={reloadKey}
        title={hint.name}
        {...(hint.modifiedTime === undefined ? {} : { version: hint.modifiedTime })}
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
      authority="resolved"
      breadcrumb={file.breadcrumb}
      canEdit={file.canEdit}
      driveId={file.driveId}
      fileId={file.id}
      mimeType={file.mimeType}
      onReload={() => {
        reloadCurrent();
      }}
      parentFolderId={file.parentFolderId}
      refreshModifiedTime={async () => {
        const refreshed = await refetchFile();
        if (refreshed.isError) throw refreshed.error;
        if (!refreshed.data?.modifiedTime) {
          throw new Error("Couldn’t verify the document’s latest revision. Please try again.");
        }
        return refreshed.data.modifiedTime;
      }}
      reloadKey={reloadKey}
      title={file.name}
      {...(file.modifiedTime === undefined ? {} : { version: file.modifiedTime })}
    />
  );
}

interface FilePageBodyProps {
  authority: "pending" | "resolved";
  breadcrumb: BreadcrumbItem[];
  canEdit: boolean;
  driveId: string | undefined;
  fileId: string;
  mimeType: string;
  onReload: () => void;
  parentFolderId: string | undefined;
  refreshModifiedTime?: () => Promise<string>;
  reloadKey: number;
  title: string;
  version?: string;
}

function FilePageBody({
  authority,
  breadcrumb,
  canEdit,
  driveId,
  fileId,
  mimeType,
  onReload,
  parentFolderId,
  refreshModifiedTime,
  reloadKey,
  title,
  version,
}: FilePageBodyProps) {
  const userId = useBrowserUserId();
  const setActive = useDocViewerStore((s) => s.setActive);
  const clearActive = useDocViewerStore((s) => s.clearActive);
  const setSlot = useDocViewerStore((s) => s.setSlot);
  const viewerOwner = useRef(Symbol("document-viewer"));
  const setOwnedSlot = (slot: HTMLElement | null) => {
    setSlot(slot, viewerOwner.current);
  };
  const displayTitle = displayFileName(title, mimeType);
  const mobileReadUrl = authority === "resolved" ? mobileReadUrlFor(mimeType, fileId) : null;
  useDocumentTitle(displayTitle);

  useEffect(() => {
    markPaint(fileId, "toolbarPaint");
  }, [fileId]);
  useLayoutEffect(() => {
    const owner = viewerOwner.current;
    if (authority !== "resolved") {
      clearActive(owner);
      return;
    }
    setActive(
      {
        fileId,
        mimeType,
        reloadKey,
        title: displayTitle,
        ...(!(version === undefined) && { version }),
      },
      owner,
    );
    return () => {
      clearActive(owner);
    };
  }, [authority, clearActive, displayTitle, fileId, mimeType, reloadKey, setActive, version]);
  useEffect(() => {
    if (authority === "resolved" && userId) {
      saveLastDoc(userId, { fileId, mimeType, title: displayTitle });
    }
  }, [authority, displayTitle, fileId, mimeType, userId]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <WikiToolbar
        authority={authority}
        breadcrumb={breadcrumb}
        canEdit={canEdit}
        driveId={driveId}
        fileId={fileId}
        mimeType={mimeType}
        {...(version === undefined ? {} : { modifiedTime: version })}
        onReload={onReload}
        parentFolderId={parentFolderId}
        {...(refreshModifiedTime === undefined ? {} : { refreshModifiedTime })}
        title={displayTitle}
      />
      {mobileReadUrl === null ? null : (
        <a
          className="border-border bg-background hover:bg-accent focus-visible:ring-ring flex shrink-0 items-center justify-between gap-2 border-b px-4 py-2 text-sm focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset md:hidden"
          href={mobileReadUrl}
          rel="noopener noreferrer"
          target="_blank"
        >
          Read document
          <span aria-hidden="true">↗</span>
        </a>
      )}
      <div className="flex-1 overflow-hidden" ref={setOwnedSlot}>
        {authority === "pending" ? <DocumentLoading /> : null}
      </div>
    </div>
  );
}

export function FilePagePending() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading file"
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      <header className="border-border bg-background flex h-10 shrink-0 items-center gap-2 overflow-hidden border-b px-4">
        <Skeleton className="size-4 shrink-0 rounded-sm" />
        <Skeleton className="h-4 w-48" />
        <div className="ml-auto flex items-center gap-2">
          <Skeleton className="h-6 w-36 sm:w-64" />
          <Skeleton className="size-6 sm:w-24" />
          <Skeleton className="hidden h-6 w-28 md:block" />
        </div>
      </header>
      <div className="flex-1 overflow-hidden">
        <DocumentLoading />
      </div>
    </div>
  );
}
