import { Skeleton } from "@/components/ui/skeleton";

export function DocSkeleton() {
  return (
    <div className="bg-background flex h-full w-full justify-center overflow-hidden px-6 py-10">
      <div className="w-full max-w-[816px] space-y-7">
        <Skeleton className="h-9 w-2/3" />
        <div className="space-y-3.5">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>
        <div className="space-y-3.5">
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-11/12" />
        </div>
      </div>
    </div>
  );
}
