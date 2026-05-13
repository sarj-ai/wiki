import { cn } from "@/lib/utils";

// Native viewBox of the Sarj wordmark SVGs in ``public/brand``. Used
// to compute the rendered width from a caller's height (the ``size``
// prop) so the wordmark never stretches.
const LOGO_VIEWBOX_WIDTH = 977.19;
const LOGO_VIEWBOX_HEIGHT = 528.06;
const ASPECT_RATIO = LOGO_VIEWBOX_WIDTH / LOGO_VIEWBOX_HEIGHT;

interface SarjLogoProps {
  /** Height in px; width is computed from the wordmark's aspect ratio. */
  size?: number;
  className?: string;
  priority?: boolean;
}

/**
 * Sarj wordmark logo. Renders as a plain ``<img>`` rather than
 * ``next/image`` so we can serve the SVG directly without enabling
 * Next's ``dangerouslyAllowSVG`` flag (which lifts SVG content
 * restrictions across the whole image pipeline). The SVG asset is
 * ~3.5kb so the loss of next/image's optimisation pass is immaterial.
 *
 * Light + dark variants ship as separate files; CSS toggles which one
 * is visible based on the ``.dark`` class on ``<html>`` (next-themes).
 */
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
