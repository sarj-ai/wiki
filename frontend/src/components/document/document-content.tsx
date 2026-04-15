"use client";

import { FileText, Pencil, RefreshCw } from "lucide-react";
import { DocumentIframe } from "@/components/document/document-iframe";
import { DocumentSkeleton } from "@/components/document/document-skeleton";
import { DocViewer } from "@/components/document/doc-viewer";
import { ContentLoadingSkeleton } from "@/components/document/content-loading-skeleton";
import { DocumentPendingState } from "@/components/document/document-pending-state";
import { isDocEmpty } from "@/lib/page-utils";
import type { PageContent } from "@/types";

interface DocumentContentProps {
  // State flags
  isPending: boolean;
  showIframe: boolean;
  isContentReady: boolean;
  isEditing: boolean;
  focusMode: boolean;

  // Data
  googleDriveId: string;
  content: PageContent | undefined;

  // Pending state
  pendingElapsed: number;
  retrying: boolean;
  onRetry: () => void;
  onDeleteStale: () => void;

  // Actions
  onStartEditing: () => void;
  onRefetchContent: () => void;
}

export function DocumentContent({
  isPending,
  showIframe,
  isContentReady,
  isEditing,
  focusMode,
  googleDriveId,
  content,
  pendingElapsed,
  retrying,
  onRetry,
  onDeleteStale,
  onStartEditing,
  onRefetchContent,
}: DocumentContentProps) {
  if (isPending) {
    return (
      <DocumentPendingState
        pendingElapsed={pendingElapsed}
        retrying={retrying}
        onRetry={onRetry}
        onDelete={onDeleteStale}
      />
    );
  }

  if (showIframe) {
    return (
      <div
        className={`animate-in fade-in flex flex-1 duration-200 ${focusMode ? "" : "px-6 pb-6"}`}
      >
        <DocumentIframe googleDriveId={googleDriveId} />
      </div>
    );
  }

  if (!isContentReady) {
    return <ContentLoadingSkeleton />;
  }

  if (content?.tabs && !isDocEmpty(content.tabs)) {
    return (
      <div className="animate-in fade-in px-6 pb-6 duration-200">
        <div className={`mx-auto py-4 ${focusMode ? "max-w-6xl" : "max-w-4xl"}`}>
          <DocViewer tabs={content.tabs} />
        </div>
      </div>
    );
  }

  if (content?.tabs !== undefined) {
    return (
      <div className="animate-in fade-in flex flex-1 items-center justify-center duration-300">
        <div className="flex flex-col items-center gap-5">
          <div className="empty-doc-icon">
            <FileText className="text-muted-foreground size-8" />
          </div>
          <div className="text-center">
            <h3 className="text-foreground text-base font-medium">This document is empty</h3>
            <p className="text-muted-foreground mt-1 text-sm">
              Start writing by switching to edit mode
            </p>
          </div>
          <button type="button" onClick={onStartEditing} className="empty-doc-edit-btn">
            <Pencil className="size-4" />
            Open editor
          </button>
          <div className="empty-doc-hint">
            <span className="empty-doc-hint-arrow" />
            <span className="text-muted-foreground text-xs">
              or click <Pencil className="mx-0.5 inline size-3" /> in the toolbar above
            </span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <p className="text-muted-foreground text-sm">Unable to load document content.</p>
        <button
          type="button"
          onClick={onRefetchContent}
          className="text-primary flex items-center gap-2 text-sm hover:underline"
        >
          <RefreshCw className="size-4" />
          Retry
        </button>
      </div>
    </div>
  );
}
