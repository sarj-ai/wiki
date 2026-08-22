"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { type ActiveDoc, useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { embedUrlFor } from "@/lib/file-urls";
import { markNav } from "@/lib/perf-marks";
import { cn } from "@/lib/utils";
import { MIME_HTML } from "@/types/wiki";

interface SlotRect {
  height: number;
  left: number;
  top: number;
  width: number;
}

const MAX_MOUNTED = 4;

export const DOCUMENT_LOADING_DELAY_MS = 150;
export const DOCUMENT_LOADING_EXIT_MS = 160;
export const DOCUMENT_LOADING_MIN_MS = 300;
export const DOCUMENT_LOADING_SLOW_MS = 10_000;

export const HTML_IFRAME_SANDBOX = "allow-popups allow-popups-to-escape-sandbox allow-scripts";
export const HTML_IFRAME_CLASS_NAME = "html-theme-frame block h-full w-full border-0";

export function htmlContentUrl(fileId: string, version?: string): string {
  const query = new URLSearchParams({ version: version ?? "current" });
  return `/api/files/${encodeURIComponent(fileId)}/content?${query.toString()}`;
}

export const DocViewerHost = () => {
  const active = useDocViewerStore((s) => s.active);
  const pool = useDocViewerStore((s) => s.pool);
  const prewarm = useDocViewerStore((s) => s.prewarm);
  const warm = useDocViewerStore((s) => s.warm);
  const slot = useDocViewerStore((s) => s.slot);
  const [rect, setRect] = useState<null | SlotRect>(null);

  const mounted: ActiveDoc[] = [];
  const seen = new Set<string>();
  for (const entry of docCandidates(pool, prewarm, warm)) {
    if (entry.mimeType === MIME_HTML && entry.fileId !== active?.fileId) continue;
    if (seen.has(entry.fileId)) continue;
    seen.add(entry.fileId);
    mounted.push(entry);
    if (mounted.length >= MAX_MOUNTED) break;
  }

  useLayoutEffect(() => {
    if (!slot) return;
    const sync = () => {
      const r = slot.getBoundingClientRect();
      const next = { height: r.height, left: r.left, top: r.top, width: r.width };
      setRect((previous) =>
        previous?.height === next.height &&
        previous.left === next.left &&
        previous.top === next.top &&
        previous.width === next.width
          ? previous
          : next,
      );
    };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(slot);
    window.addEventListener("resize", sync);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", sync);
    };
  }, [slot]);

  const visible = active !== null && slot !== null && rect !== null;

  return (
    <div
      aria-hidden={!visible}
      className={cn("fixed z-10", !visible && "pointer-events-none invisible")}
      style={visible ? { ...rect } : { height: 0, left: 0, top: 0, width: 0 }}
    >
      {mounted.map((entry) => (
        <div
          aria-hidden={viewerKey(entry) !== (active === null ? null : viewerKey(active))}
          className={cn("absolute inset-0", entry.fileId !== active?.fileId && "hidden")}
          key={entry.fileId}
        >
          <PooledIframe
            active={viewerKey(entry) === (active === null ? null : viewerKey(active))}
            fileId={entry.fileId}
            key={viewerKey(entry)}
            mimeType={entry.mimeType}
            title={entry.title}
            {...(entry.version === undefined ? {} : { version: entry.version })}
          />
        </div>
      ))}
    </div>
  );
};

export function viewerKey(entry: ActiveDoc): string {
  const contentVersion = entry.mimeType === MIME_HTML ? (entry.version ?? "current") : "stable";
  return `${entry.fileId}:${entry.reloadKey}:${contentVersion}`;
}

interface PooledIframeProps {
  active: boolean;
  fileId: string;
  mimeType: string;
  title: string;
  version?: string;
}

const PooledIframe = ({ active, fileId, mimeType, title, version }: PooledIframeProps) => {
  if (mimeType === MIME_HTML) {
    return <HtmlIframe active={active} fileId={fileId} title={title} version={version} />;
  }
  return <DrivePreviewIframe active={active} fileId={fileId} mimeType={mimeType} title={title} />;
};

const DrivePreviewIframe = ({ active, fileId, mimeType, title }: PooledIframeProps) => {
  const src = embedUrlFor(mimeType, fileId);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    markNav(fileId, "iframeMount");
  }, [fileId]);

  return (
    <DocumentFrame active={active} loading={isLoading}>
      <iframe
        allow="clipboard-read; clipboard-write"
        className="block size-full border-0"
        ref={attachIframeLoadListener.bind(undefined, () => {
          markIframeLoaded(fileId, setIsLoading);
        })}
        src={src}
        title={title}
      />
    </DocumentFrame>
  );
};

function attachIframeLoadListener(onLoaded: () => void, iframe: HTMLIFrameElement | null) {
  if (!iframe) return;
  iframe.addEventListener("load", onLoaded);
  return () => {
    iframe.removeEventListener("load", onLoaded);
  };
}

function markIframeLoaded(fileId: string, setIsLoading: (isLoading: boolean) => void): void {
  markNav(fileId, "iframeLoad");
  setIsLoading(false);
}

function* docCandidates(
  pool: ActiveDoc[],
  prewarm: ActiveDoc | null,
  warm: ActiveDoc[],
): Generator<ActiveDoc> {
  yield* pool;
  if (prewarm) yield prewarm;
  yield* warm;
}

function HtmlIframe({
  active,
  fileId,
  title,
  version,
}: {
  active: boolean;
  fileId: string;
  title: string;
  version: string | undefined;
}) {
  const [isLoading, setIsLoading] = useState(true);
  return (
    <DocumentFrame active={active} loading={isLoading}>
      <iframe
        className={HTML_IFRAME_CLASS_NAME}
        ref={attachIframeLoadListener.bind(undefined, () => {
          markIframeLoaded(fileId, setIsLoading);
        })}
        referrerPolicy="no-referrer"
        sandbox={HTML_IFRAME_SANDBOX}
        src={htmlContentUrl(fileId, version)}
        title={title}
      />
    </DocumentFrame>
  );
}

type LoadingOverlayPhase = "exiting" | "hidden" | "masked-exiting" | "masked" | "visible";

function DocumentFrame({
  active,
  children,
  loading,
}: {
  active: boolean;
  children: React.ReactNode;
  loading: boolean;
}) {
  const { phase, slow } = useDocumentLoading(active, loading);
  const announced = active && (phase === "exiting" || phase === "visible");

  return (
    <div aria-busy={announced || undefined} className="relative size-full">
      {children}
      {phase === "hidden" ? null : (
        <div
          aria-hidden={!announced}
          className={cn(
            "bg-background absolute inset-0 z-10 overflow-hidden opacity-100 transition-opacity duration-[160ms]",
            (phase === "exiting" || phase === "masked-exiting") && "opacity-0",
            "motion-reduce:transition-none",
          )}
          data-document-loading-overlay="true"
          data-loading-phase={phase}
        >
          {announced ? (
            <DocSkeleton
              animated={false}
              status={
                <div
                  aria-atomic="true"
                  aria-live="polite"
                  className="text-muted-foreground flex min-h-5 items-center justify-center gap-2 px-6 text-center text-xs font-medium"
                  role="status"
                >
                  <Loader2
                    aria-hidden="true"
                    className="text-primary size-3.5 shrink-0 animate-spin motion-reduce:hidden"
                  />
                  <span>{slow ? "Still loading document…" : "Loading document…"}</span>
                </div>
              }
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

function useDocumentLoading(
  active: boolean,
  loading: boolean,
): { phase: LoadingOverlayPhase; slow: boolean } {
  const reducedMotion = useReducedMotion();
  const [phaseState, setPhaseState] = useState<LoadingOverlayPhase>("masked");
  const [slow, setSlow] = useState(false);
  const generationRef = useRef(0);
  const phaseRef = useRef<LoadingOverlayPhase>("masked");
  const visibleSinceRef = useRef(0);

  /* eslint-disable react-hooks/set-state-in-effect -- Active/loading transitions intentionally reset this timer-driven UI state machine before scheduling its next phase. */
  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const animationFrames: number[] = [];
    const timers: number[] = [];
    const isCurrent = () => generationRef.current === generation;
    const setPhase = (phase: LoadingOverlayPhase) => {
      phaseRef.current = phase;
      setPhaseState(phase);
    };
    const cleanup = () => {
      if (generationRef.current === generation) generationRef.current += 1;
      for (const timer of timers) window.clearTimeout(timer);
      for (const frame of animationFrames) window.cancelAnimationFrame(frame);
    };

    if (!active) {
      setSlow(false);
      setPhase(loading ? "masked" : "hidden");
      return cleanup;
    }

    if (loading) {
      setSlow(false);
      if (
        phaseRef.current === "exiting" ||
        phaseRef.current === "hidden" ||
        phaseRef.current === "masked-exiting"
      ) {
        setPhase("masked");
      }
      if (phaseRef.current !== "visible") {
        timers.push(
          window.setTimeout(() => {
            if (!isCurrent()) return;
            visibleSinceRef.current = Date.now();
            setPhase("visible");
          }, DOCUMENT_LOADING_DELAY_MS),
        );
      }
      timers.push(
        window.setTimeout(() => {
          if (isCurrent()) setSlow(true);
        }, DOCUMENT_LOADING_SLOW_MS),
      );
      return cleanup;
    }

    setSlow(false);
    if (phaseRef.current === "hidden") return cleanup;
    const minimumRemaining =
      phaseRef.current === "visible"
        ? Math.max(0, visibleSinceRef.current + DOCUMENT_LOADING_MIN_MS - Date.now())
        : 0;
    timers.push(
      window.setTimeout(() => {
        if (!isCurrent()) return;
        animationFrames.push(
          window.requestAnimationFrame(() => {
            if (!isCurrent()) return;
            animationFrames.push(
              window.requestAnimationFrame(() => {
                if (!isCurrent()) return;
                if (reducedMotion) {
                  setPhase("hidden");
                  return;
                }
                setPhase(phaseRef.current === "visible" ? "exiting" : "masked-exiting");
                timers.push(
                  window.setTimeout(() => {
                    if (isCurrent()) setPhase("hidden");
                  }, DOCUMENT_LOADING_EXIT_MS),
                );
              }),
            );
          }),
        );
      }, minimumRemaining),
    );
    return cleanup;
  }, [active, loading, reducedMotion]);
  /* eslint-enable react-hooks/set-state-in-effect -- Timer state transitions complete above. */

  return { phase: phaseState, slow };
}

function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeToReducedMotion, reducedMotionSnapshot, () => false);
}

function subscribeToReducedMotion(callback: () => void): () => void {
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  query.addEventListener("change", callback);
  return () => {
    query.removeEventListener("change", callback);
  };
}

function reducedMotionSnapshot(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
