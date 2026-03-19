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
      <div className={`flex flex-1 animate-in fade-in duration-200 ${focusMode ? "" : "px-6 pb-6"}`}>
        <DocumentIframe googleDriveId={googleDriveId} />
      </div>
    );
  }

  if (!isContentReady) {
    return <ContentLoadingSkeleton />;
  }

  if (content?.tabs && !isDocEmpty(content.tabs)) {
    return (
      <div className="px-6 pb-6 animate-in fade-in duration-200">
        <div className="mx-auto max-w-4xl py-4">
          <DocViewer tabs={content.tabs} />
        </div>
      </div>
    );
  }

  if (content?.tabs !== undefined) {
    return (
      <div className="flex-1 flex items-center justify-center animate-in fade-in duration-300">
        <div className="flex flex-col items-center gap-5">
          <div className="empty-doc-icon">
            <FileText className="size-8 text-muted-foreground" />
          </div>
          <div className="text-center">
            <h3 className="text-base font-medium text-foreground">
              This document is empty
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Start writing by switching to edit mode
            </p>
          </div>
          <button
            type="button"
            onClick={onStartEditing}
            className="empty-doc-edit-btn"
          >
            <Pencil className="size-4" />
            Open editor
          </button>
          <div className="empty-doc-hint">
            <span className="empty-doc-hint-arrow" />
            <span className="text-xs text-muted-foreground">
              or click <Pencil className="inline size-3 mx-0.5" /> in the toolbar above
            </span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <p className="text-sm text-muted-foreground">
          Unable to load document content.
        </p>
        <button
          type="button"
          onClick={onRefetchContent}
          className="flex items-center gap-2 text-sm text-primary hover:underline"
        >
          <RefreshCw className="size-4" />
          Retry
        </button>
      </div>
    </div>
  );
}
