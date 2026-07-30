"use client";

import { Button } from "@/components/ui/button";

export default function GlobalError({ reset }: { reset: () => void }) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground m-0 flex min-h-screen items-center justify-center font-sans">
        <div className="p-8 text-center">
          <h1 className="mb-2 text-xl">Something went wrong</h1>
          <p className="mb-6 opacity-70">
            Sarj Wiki hit an unexpected error. Reloading usually fixes it.
          </p>
          <Button
            className="rounded-lg px-5 py-2 font-[inherit]"
            onClick={reset}
            type="button"
            variant="outline"
          >
            Try again
          </Button>
        </div>
      </body>
    </html>
  );
}
