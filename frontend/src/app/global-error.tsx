"use client";

export default function GlobalError({ reset }: { reset: () => void }) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground font-system m-0 flex min-h-svh items-center justify-center px-4 py-6">
        <div className="p-8 text-center">
          <h1 className="mb-2 text-xl">Something went wrong</h1>
          <p className="mb-6 opacity-70">
            Sarj Wiki hit an unexpected error. Reloading usually fixes it.
          </p>
          <button
            className="bg-background cursor-pointer rounded-lg border px-5 py-2 font-[inherit]"
            onClick={reset}
            type="button"
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
