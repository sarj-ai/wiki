import { cn } from "@/lib/utils";

interface SarjLogoProps {
  size?: number;
  className?: string;
  priority?: boolean;
}

const LOGO_VIEWBOX_WIDTH = 977.19;
const LOGO_VIEWBOX_HEIGHT = 528.06;
const ASPECT_RATIO = LOGO_VIEWBOX_WIDTH / LOGO_VIEWBOX_HEIGHT;

export function SarjLogo({ size = 32, className, priority = false }: SarjLogoProps) {
  const height = size;
  const width = Math.round(size * ASPECT_RATIO);
  const loading = priority ? "eager" : "lazy";
  const fetchPriority = priority ? "high" : "auto";
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- deliberate: plain <img> for SVG sidesteps next/image's dangerouslyAllowSVG flag */}
      <img
        src="/brand/sarj-logo-light.svg"
        alt="Sarj"
        width={width}
        height={height}
        loading={loading}
        fetchPriority={fetchPriority}
        className={cn("block dark:hidden", className)}
      />
      {/* eslint-disable-next-line @next/next/no-img-element -- deliberate: see light variant above */}
      <img
        src="/brand/sarj-logo-dark.svg"
        alt="Sarj"
        width={width}
        height={height}
        loading={loading}
        fetchPriority={fetchPriority}
        className={cn("hidden dark:block", className)}
      />
    </>
  );
}
