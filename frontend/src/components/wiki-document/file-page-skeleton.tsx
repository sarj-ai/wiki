import { FileLoadingIndicator } from "@/components/wiki-document/file-loading-indicator";

/**
 * Full-viewport branded loading state shown while the file detail
 * query resolves. The actual visual is shared with the iframe-reload
 * overlay in ``FileViewer`` via ``FileLoadingIndicator``; this wrapper
 * just supplies the full-page chrome (viewport height + aria label)
 * appropriate for the top-of-page render path.
 */
export function FilePageSkeleton() {
  return (
    <div
      className="flex h-[100dvh] flex-col items-center justify-center"
      aria-busy="true"
      aria-label="Loading file"
    >
      <FileLoadingIndicator />
    </div>
  );
}
