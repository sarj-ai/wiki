"use client";

import { useState } from "react";

import { FileLoadingIndicator } from "@/components/wiki-document/file-loading-indicator";
import { embedUrlFor } from "@/lib/file-urls";
import { cn } from "@/lib/utils";

interface FileViewerProps {
  fileId: string;
  mimeType: string;
  title: string;
  // Bumped by the parent page when the toolbar's Reload button fires,
  // baked into the iframe ``key`` so React unmounts + remounts the
  // iframe. Used as the user's recovery path for Google's embedded
  // editor going blank/black after long browser-tab backgrounding —
  // Google's editor holds a WebGL/websocket session inside the iframe
  // that browsers can evict in the background, and Google's restore
  // path is unreliable. We can't reach into the cross-origin iframe to
  // detect or fix the black frame, so we hand the user a one-click way
  // to start a fresh editor session.
  reloadKey?: number;
}

interface FileViewerInnerProps {
  fileId: string;
  mimeType: string;
  title: string;
}

interface PoolEntry {
  fileId: string;
  mimeType: string;
  title: string;
  reloadKey: number;
}

const MAX_POOL = 3;

function FileViewerInner({ fileId, mimeType, title }: FileViewerInnerProps) {
  const src = embedUrlFor(mimeType, fileId);
  const [isLoading, setIsLoading] = useState(true);

  const attachLoadListener = (iframe: HTMLIFrameElement | null) => {
    if (!iframe) return;
    const handleLoad = () => {
      setIsLoading(false);
    };
    iframe.addEventListener("load", handleLoad);
    return () => {
      iframe.removeEventListener("load", handleLoad);
    };
  };

  return (
    <div className="relative h-full w-full">
      {isLoading && (
        <div
          className="bg-background absolute inset-0 z-10 flex items-center justify-center"
          aria-label="Loading document"
        >
          <FileLoadingIndicator />
        </div>
      )}
      <iframe
        ref={attachLoadListener}
        title={title}
        src={src}
        className="block h-full w-full border-0"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}

// Full-height iframe for the selected file. Google Workspace types use
// their `edit?embedded=true` variants so comments/suggesting/version
// history all work natively; PDFs and images fall back to Drive's
// preview endpoint inside the same frame.
export function FileViewer({ fileId, mimeType, title, reloadKey = 0 }: FileViewerProps) {
  const [pool, setPool] = useState<PoolEntry[]>([{ fileId, mimeType, title, reloadKey }]);
  const active = pool[0];
  if (active?.fileId !== fileId || active.reloadKey !== reloadKey || active.mimeType !== mimeType) {
    setPool(
      [
        { fileId, mimeType, title, reloadKey },
        ...pool.filter((entry) => entry.fileId !== fileId),
      ].slice(0, MAX_POOL),
    );
  }

  return (
    <div className="relative h-full w-full">
      {pool.map((entry) => (
        <div
          key={entry.fileId}
          className={cn("absolute inset-0", entry.fileId !== fileId && "hidden")}
        >
          <FileViewerInner
            key={`${entry.fileId}:${entry.reloadKey}`}
            fileId={entry.fileId}
            mimeType={entry.mimeType}
            title={entry.title}
          />
        </div>
      ))}
    </div>
  );
}
