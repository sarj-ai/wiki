import { cn } from "@/lib/utils";

interface SarjLogoProps {
  className?: string;
  priority?: boolean;
  size?: number;
}

const LOGO_VIEWBOX_WIDTH = 977.19;
const LOGO_VIEWBOX_HEIGHT = 528.06;
const ASPECT_RATIO = LOGO_VIEWBOX_WIDTH / LOGO_VIEWBOX_HEIGHT;

export function SarjLogo({ className, priority = false, size = 32 }: SarjLogoProps) {
  const height = size;
  const width = Math.round(size * ASPECT_RATIO);
  const loading = priority ? "eager" : "lazy";
  const fetchPriority = priority ? "high" : "auto";
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- deliberate: plain <img> for SVG sidesteps next/image's dangerouslyAllowSVG flag */}
      <img
        alt="Sarj"
        className={cn("block dark:hidden", className)}
        fetchPriority={fetchPriority}
        height={height}
        loading={loading}
        src="/brand/sarj-logo-light.svg"
        width={width}
      />
      {/* eslint-disable-next-line @next/next/no-img-element -- deliberate: see light variant above */}
      <img
        alt="Sarj"
        className={cn("hidden dark:block", className)}
        fetchPriority={fetchPriority}
        height={height}
        loading={loading}
        src="/brand/sarj-logo-dark.svg"
        width={width}
      />
    </>
  );
}
