"use client";

import { AlertCircle, Loader2, RefreshCw } from "lucide-react";

interface DocumentPendingStateProps {
  pendingElapsed: number;
  retrying: boolean;
  onRetry: () => void;
  onDelete: () => void;
}

export function DocumentPendingState({
  pendingElapsed,
  retrying,
  onRetry,
  onDelete,
}: DocumentPendingStateProps) {
  const isStale = pendingElapsed > 30 && !retrying;

  return (
    <div className="animate-in fade-in flex flex-1 items-center justify-center duration-300">
      <div className="flex max-w-sm flex-col items-center gap-6 text-center">
        <div className="relative">
          <div className="bg-muted flex size-16 items-center justify-center rounded-2xl">
            {isStale ? (
              <AlertCircle className="text-destructive size-8" />
            ) : (
              <Loader2 className="text-muted-foreground size-8 animate-spin" />
            )}
          </div>
          {!isStale && (
            <div className="bg-background border-muted absolute -right-1 -bottom-1 flex size-5 items-center justify-center rounded-full border-2">
              <svg
                className="text-muted-foreground size-3"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
          )}
        </div>
        <div>
          <h3 className="text-foreground text-base font-medium">
            {isStale ? "Something went wrong" : "Setting up your document"}
          </h3>
          <p className="text-muted-foreground mt-1.5 text-sm leading-relaxed">
            {isStale
              ? "The document couldn't be created in Google Drive. You can retry or delete this page and start over."
              : "Creating a new Google Doc and syncing with Drive. This usually takes a few seconds."}
          </p>
        </div>
        {isStale ? (
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onRetry}
              className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors"
            >
              <RefreshCw className="size-4" />
              Retry
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="border-border text-muted-foreground hover:text-foreground hover:bg-muted inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium transition-colors"
            >
              Delete page
            </button>
          </div>
        ) : (
          <div className="text-muted-foreground/60 flex items-center gap-2 text-xs">
            <div className="size-1.5 animate-pulse rounded-full bg-green-500" />
            Syncing with Google Drive
          </div>
        )}
      </div>
    </div>
  );
}
