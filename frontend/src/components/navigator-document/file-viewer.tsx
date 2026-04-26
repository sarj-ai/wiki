"use client";

import { embedUrlFor } from "@/lib/file-urls";

interface FileViewerProps {
  fileId: string;
  mimeType: string;
  title: string;
}

// Full-height iframe for the selected file. Google Workspace types use
// their `edit?embedded=true` variants so comments/suggesting/version
// history all work natively; PDFs and images fall back to Drive's
// preview endpoint inside the same frame.
export function FileViewer({ fileId, mimeType, title }: FileViewerProps) {
  const src = embedUrlFor(mimeType, fileId);
  return (
    <iframe
      title={title}
      src={src}
      className="block h-full w-full border-0"
      allow="clipboard-read; clipboard-write"
    />
  );
}
