import { Skeleton } from "@/components/ui/skeleton";
import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";

export function FilePageSkeleton() {
  return (
    <div className="flex h-[100dvh] flex-col" aria-busy="true" aria-label="Loading file">
      <div className="border-border flex items-center gap-2 border-b px-4 py-2">
        <Skeleton className="size-4 rounded" />
        <Skeleton className="h-4 w-44" />
        <div className="ml-auto flex gap-2">
          <Skeleton className="h-6 w-8" />
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-28" />
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <DocSkeleton />
      </div>
    </div>
  );
}
