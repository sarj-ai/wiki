"use client";

import { Skeleton } from "@/components/ui/skeleton";

export function SidebarSkeleton() {
  return (
    <div className="flex flex-col gap-1.5 px-2 py-1">
      {/* Root-level item */}
      <div className="flex items-center gap-2 px-2 py-1.5">
        <Skeleton className="size-4 shrink-0 rounded" />
        <Skeleton className="h-4 w-[140px] rounded" />
      </div>
      {/* Indented child */}
      <div className="flex items-center gap-2 px-2 py-1.5 pl-6">
        <Skeleton className="size-4 shrink-0 rounded" />
        <Skeleton className="h-4 w-[110px] rounded" />
      </div>
      {/* Indented child */}
      <div className="flex items-center gap-2 px-2 py-1.5 pl-6">
        <Skeleton className="size-4 shrink-0 rounded" />
        <Skeleton className="h-4 w-[90px] rounded" />
      </div>
      {/* Root-level item */}
      <div className="flex items-center gap-2 px-2 py-1.5">
        <Skeleton className="size-4 shrink-0 rounded" />
        <Skeleton className="h-4 w-[120px] rounded" />
      </div>
    </div>
  );
}
