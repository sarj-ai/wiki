"use client";

import { Skeleton } from "@/components/ui/skeleton";

export function DocumentSkeleton() {
  return (
    <div className="flex h-full min-h-svh flex-col">
      <div className="flex flex-col gap-4 px-6 pt-6 pb-4">
        {/* Breadcrumb */}
        <Skeleton className="h-4 w-[180px] rounded" />

        {/* Icon */}
        <Skeleton className="size-12 rounded-lg" />

        {/* Title */}
        <Skeleton className="h-8 w-[320px] rounded" />

        {/* Subtitle / workspace badge area */}
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-[100px] rounded-full" />
          <Skeleton className="h-5 w-[60px] rounded-full" />
        </div>
      </div>

      {/* Iframe placeholder */}
      <div className="flex min-h-0 flex-1 px-6 pb-6">
        <Skeleton className="h-full min-h-[400px] w-full rounded-lg" />
      </div>
    </div>
  );
}
