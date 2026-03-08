"use client";

import { useMemo, useState } from "react";

const DRIVE_ID_PATTERN = /^[a-zA-Z0-9_-]+$/;

interface DocumentIframeProps {
  googleDriveId: string;
}

export function DocumentIframe({ googleDriveId }: DocumentIframeProps) {
  const [isLoading, setIsLoading] = useState(true);

  const embedUrl = useMemo(() => {
    if (!DRIVE_ID_PATTERN.test(googleDriveId)) {
      return null;
    }
    return `https://docs.google.com/document/d/${googleDriveId}/edit?embedded=true`;
  }, [googleDriveId]);

  if (!embedUrl) {
    return (
      <div className="flex flex-1 items-center justify-center min-h-0">
        <p className="text-sm text-muted-foreground">Invalid document ID.</p>
      </div>
    );
  }

  return (
    <div className="relative flex-1 min-h-0">
      {/* Loading overlay */}
      {isLoading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg border border-border bg-card">
          <div className="flex flex-col items-center gap-3">
            <div className="size-8 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Loading document...
            </p>
          </div>
        </div>
      )}

      {/* Google Docs iframe */}
      <iframe
        src={embedUrl}
        title="Google Doc"
        className="size-full rounded-lg border border-border bg-card"
        onLoad={() => setIsLoading(false)}
        allow="clipboard-write"
        sandbox="allow-same-origin allow-scripts allow-popups allow-forms allow-popups-to-escape-sandbox"
      />
    </div>
  );
}
