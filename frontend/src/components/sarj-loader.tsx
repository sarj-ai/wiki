import { cn } from "@/lib/utils";

export function SarjLoader({
  className,
  label = "Loading…",
}: {
  className?: string;
  label?: string;
}) {
  return (
    <div
      aria-label={label}
      className={cn("flex flex-col items-center justify-center gap-4", className)}
      role="status"
    >
      <picture className="block aspect-544/182 w-32 max-w-full">
        <source
          media="(prefers-reduced-motion: reduce)"
          srcSet="/brand/sarj-loader-still-12132f41.svg"
        />
        <img
          alt=""
          className="block h-auto w-full dark:invert"
          decoding="async"
          height={182}
          src="/brand/sarj-loader-12132f41.svg"
          width={544}
        />
      </picture>
      <span className="text-muted-foreground text-center text-xs font-medium">{label}</span>
    </div>
  );
}
