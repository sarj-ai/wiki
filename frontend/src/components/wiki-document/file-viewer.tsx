"use client";

import { useState } from "react";

import { FileLoadingIndicator } from "@/components/wiki-document/file-loading-indicator";
import { embedUrlFor } from "@/lib/file-urls";

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

function FileViewerInner({ fileId, mimeType, title }: FileViewerProps) {
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
//
// The component is split in two so changes to ``fileId`` or
// ``reloadKey`` remount the inner component (and the iframe + loading
// state it owns) cleanly: a thin outer ``FileViewer`` sets a composite
// ``key`` on ``FileViewerInner``, where the actual ``<iframe>`` and the
// ``isLoading`` state live. This avoids the eslint-flagged "reset state
// in effect" pattern, and also rescues two black-frame edge cases:
// fast doc-to-doc navigation (rapid ``src`` mutation can leave Google's
// inner editor torn down without re-initialising) and tab-backgrounding
// (browsers evict iframe resources; Google's restore path is unreliable
// — the Reload button bumps ``reloadKey`` for explicit recovery).
//
// While the new iframe loads, a branded overlay sits on top so the
// reload click has visible feedback rather than a few seconds of stale
// or empty frame. ``onLoad`` clears it as soon as the document fires.
export function FileViewer(props: FileViewerProps) {
  const key = `${props.fileId}:${props.reloadKey ?? 0}`;
  return <FileViewerInner key={key} {...props} />;
}
