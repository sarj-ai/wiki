"use client";

import { Button } from "@/components/ui/button";
import type { useDrives } from "@/hooks/use-wiki-tree";

export function DriveListFeedback({
  empty,
  emptyMessage = "No available drives.",
  query,
}: {
  empty: boolean;
  emptyMessage?: string;
  query: Pick<ReturnType<typeof useDrives>, "isError" | "isFetching" | "isPending" | "refetch">;
}) {
  if (query.isPending) {
    return (
      <p className="text-muted-foreground p-3 text-sm" role="status">
        Loading drives…
      </p>
    );
  }
  if (query.isError) {
    return (
      <div className="space-y-2 p-3 text-sm" role="alert">
        <p>Drives could not be loaded.</p>
        <Button
          disabled={query.isFetching}
          onClick={() => {
            void query.refetch().catch(() => console.warn("Drive retry could not finish"));
          }}
          size="sm"
          type="button"
          variant="outline"
        >
          Retry drives
        </Button>
      </div>
    );
  }
  return empty ? <p className="text-muted-foreground p-3 text-sm">{emptyMessage}</p> : null;
}
