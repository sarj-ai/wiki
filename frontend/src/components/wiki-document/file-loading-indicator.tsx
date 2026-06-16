import { SarjLogo } from "@/components/sarj-logo";

export function FileLoadingIndicator() {
  return (
    <div className="flex flex-col items-center justify-center gap-8" aria-busy="true">
      <div className="relative flex size-24 items-center justify-center">
        <div className="bg-primary/15 absolute inset-0 animate-pulse rounded-full blur-2xl" />

        <span
          className="border-primary/30 absolute inset-0 inline-flex animate-ping rounded-full border"
          style={{ animationDuration: "2s" }}
        />

        <span
          className="border-primary/40 absolute inset-2 inline-flex animate-ping rounded-full border"
          style={{ animationDuration: "1.5s", animationDelay: "0.3s" }}
        />
        <SarjLogo size={56} priority className="relative" />
      </div>
      <div className="flex items-center gap-1.5" aria-hidden>
        <span
          className="bg-primary size-1.5 animate-bounce rounded-full"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="bg-primary size-1.5 animate-bounce rounded-full"
          style={{ animationDelay: "150ms" }}
        />
        <span
          className="bg-primary size-1.5 animate-bounce rounded-full"
          style={{ animationDelay: "300ms" }}
        />
      </div>
    </div>
  );
}
