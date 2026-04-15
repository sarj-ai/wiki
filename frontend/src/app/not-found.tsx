import Link from "next/link";
import { FileX2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Root 404 boundary — catches anything that doesn't resolve under
 * /login or the (app) route group. Deliberately standalone (no
 * sidebar) because this path runs before the user is known. Still
 * uses the Sarj theme tokens so light/dark mode flip automatically.
 */
export default function RootNotFound() {
  return (
    <div className="bg-background text-foreground flex min-h-screen items-center justify-center px-6">
      <div className="border-border bg-card text-card-foreground flex max-w-md flex-col items-center gap-4 rounded-xl border p-8 text-center shadow-sm">
        <div className="bg-muted text-muted-foreground flex size-14 items-center justify-center rounded-full">
          <FileX2 className="size-7" />
        </div>
        <div className="space-y-1.5">
          <h2 className="text-lg font-semibold tracking-tight">Page not found</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            That URL doesn&rsquo;t match anything in the Navigator. Head back to the home page and
            pick a file from the sidebar.
          </p>
        </div>
        <Button asChild>
          <Link href="/">Back to Navigator</Link>
        </Button>
      </div>
    </div>
  );
}
