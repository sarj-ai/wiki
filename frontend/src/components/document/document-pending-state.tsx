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
    <div className="flex-1 flex items-center justify-center animate-in fade-in duration-300">
      <div className="flex flex-col items-center gap-6 max-w-sm text-center">
        <div className="relative">
          <div className="size-16 rounded-2xl bg-muted flex items-center justify-center">
            {isStale ? (
              <AlertCircle className="size-8 text-destructive" />
            ) : (
              <Loader2 className="size-8 text-muted-foreground animate-spin" />
            )}
          </div>
          {!isStale && (
            <div className="absolute -bottom-1 -right-1 size-5 rounded-full bg-background border-2 border-muted flex items-center justify-center">
              <svg className="size-3 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
          )}
        </div>
        <div>
          <h3 className="text-base font-medium text-foreground">
            {isStale ? "Something went wrong" : "Setting up your document"}
          </h3>
          <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
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
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <RefreshCw className="size-4" />
              Retry
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            >
              Delete page
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted-foreground/60">
            <div className="size-1.5 rounded-full bg-green-500 animate-pulse" />
            Syncing with Google Drive
          </div>
        )}
      </div>
    </div>
  );
}
