import { SarjLogo } from "@/components/sarj-logo";

/**
 * Branded loading centrepiece — sonar rings + brand logo + bouncing dots.
 *
 * Self-centering via flex, no enforced viewport height. Wrap in a
 * full-page container for top-level page loads (see ``FilePageSkeleton``),
 * or position absolutely over the iframe for in-place reload states (see
 * ``FileViewer``).
 *
 * Composition:
 *   * Soft blurred halo in brand purple, breathing via ``animate-pulse``.
 *   * Two concentric rings rippling outward via Tailwind's
 *     ``animate-ping`` (scale + fade keyframes) at staggered durations
 *     so they read as a sonar pulse rather than a single beat.
 *   * Brand logo centred over the effects — light/dark variants ship
 *     with ``SarjLogo`` so the centrepiece is theme-aware for free.
 *   * Three brand-purple dots below, ``animate-bounce`` with staggered
 *     delays — a quiet cadence cue without resorting to text.
 */
export function FileLoadingIndicator() {
  return (
    <div className="flex flex-col items-center justify-center gap-8" aria-busy="true">
      <div className="relative flex size-24 items-center justify-center">
        {/* Halo: blurred fill that breathes in/out. */}
        <div className="bg-primary/15 absolute inset-0 animate-pulse rounded-full blur-2xl" />
        {/* Outer sonar ring — slower expansion. */}
        <span
          className="border-primary/30 absolute inset-0 inline-flex animate-ping rounded-full border"
          style={{ animationDuration: "2s" }}
        />
        {/* Inner sonar ring — slightly delayed for a multi-wave feel. */}
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
