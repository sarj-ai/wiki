"use client";

import { Skeleton } from "@/components/ui/skeleton";

export function ContentLoadingSkeleton() {
  return (
    <div className="animate-in fade-in px-6 pb-6 duration-200">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 py-4">
        <Skeleton className="h-8 w-3/5 rounded" />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-4 w-full rounded" />
          <Skeleton className="h-4 w-full rounded" />
          <Skeleton className="h-4 w-4/5 rounded" />
        </div>
        <Skeleton className="h-6 w-2/5 rounded" />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-4 w-full rounded" />
          <Skeleton className="h-4 w-full rounded" />
          <Skeleton className="h-4 w-3/4 rounded" />
        </div>
        <div className="flex flex-col gap-3">
          <Skeleton className="h-4 w-full rounded" />
          <Skeleton className="h-4 w-5/6 rounded" />
        </div>
        <div className="flex flex-col gap-2 pl-4">
          <Skeleton className="h-4 w-3/5 rounded" />
          <Skeleton className="h-4 w-2/5 rounded" />
          <Skeleton className="h-4 w-1/2 rounded" />
        </div>
      </div>
    </div>
  );
}
