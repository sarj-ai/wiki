import { FileX2 } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export function WikiNotFound({
  description = "This file isn't available. It may have been moved, deleted, or it lives outside your shared drives.",
  title = "File not found",
}: WikiNotFoundProps) {
  return (
    <div className="flex h-full min-h-0 overflow-y-auto px-4 py-6">
      <div className="border-border bg-card text-card-foreground m-auto flex max-w-md flex-col items-center gap-4 rounded-xl border p-6 text-center shadow-sm sm:p-8">
        <div className="bg-muted text-muted-foreground flex size-14 items-center justify-center rounded-full">
          <FileX2 className="size-7" />
        </div>
        <div className="space-y-1.5">
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          <p className="text-muted-foreground text-sm leading-relaxed">{description}</p>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
          <Button asChild>
            <Link href="/">Back to Sarj Wiki</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}

interface WikiNotFoundProps {
  description?: string;

  title?: string;
}
