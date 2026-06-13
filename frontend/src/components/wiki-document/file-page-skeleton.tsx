import { FileLoadingIndicator } from "@/components/wiki-document/file-loading-indicator";

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
