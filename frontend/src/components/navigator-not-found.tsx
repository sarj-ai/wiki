import Link from "next/link";
import { FileX2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface NavigatorNotFoundProps {
  /** Main heading — defaults to "File not found" but callers can tailor. */
  title?: string;
  /** Sub-text explaining the most likely reasons. */
  description?: string;
  /** Optional href for a secondary "retry" action. Home button is always present. */
  retryHref?: string;
}

/**
 * Shared empty-state view for 404-like situations. Rendered inside the
 * Navigator's (app) layout so the sidebar stays visible — the user
 * doesn't feel like they fell out of the app. Styled with the same
 * surface colors as the rest of the Navigator chrome so it blends into
 * a light- or dark-mode shell without extra theme wiring.
 */
export function NavigatorNotFound({
  title = "File not found",
  description = "This file isn't available. It may have been moved, deleted, or it lives outside your shared drives.",
  retryHref,
}: NavigatorNotFoundProps) {
  return (
    <div className="flex h-full min-h-[calc(100dvh-4rem)] items-center justify-center px-6">
      <div className="border-border bg-card text-card-foreground flex max-w-md flex-col items-center gap-4 rounded-xl border p-8 text-center shadow-sm">
        <div className="bg-muted text-muted-foreground flex size-14 items-center justify-center rounded-full">
          <FileX2 className="size-7" />
        </div>
        <div className="space-y-1.5">
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">{description}</p>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
          <Button asChild>
            <Link href="/">Back to Navigator</Link>
          </Button>
          {retryHref && (
            <Button asChild variant="outline">
              <Link href={retryHref}>Try again</Link>
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
