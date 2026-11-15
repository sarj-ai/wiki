"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import {
  type ActiveDoc,
  useDocViewerStore,
} from "@/components/wiki-document/doc-viewer-store";
import { DocumentLoading } from "@/components/wiki-document/document-loading";
import { DriveDocumentIframe } from "@/components/wiki-document/drive-document-iframe";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { type DocumentMode, embedUrlFor } from "@/lib/file-urls";
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

export const HTML_IFRAME_SANDBOX =
  "allow-popups allow-popups-to-escape-sandbox allow-scripts";
export const HTML_IFRAME_CLASS_NAME =
  "html-theme-frame block h-full w-full border-0";

export function htmlContentUrl(fileId: string, version?: string): string {
  const query = new URLSearchParams({ version: version ?? "current" });
  return `/api/files/${encodeURIComponent(fileId)}/content?${query.toString()}`;
}

export const DocViewerHost = () => {
  const { active, pool, prewarm, slot, warm } = useDocViewerStore(
    useShallow((state) => ({
      active: state.active,
      pool: state.pool,
      prewarm: state.prewarm,
      slot: state.slot,
      warm: state.warm,
    })),
  );
  const [rect, setRect] = useState<null | SlotRect>(null);

  const mounted: ActiveDoc[] = [];
  const prewarmCount = prewarm === null ? 0 : 1;
  const count = pool.length + prewarmCount + warm.length;
  for (let index = 0; index < count; index += 1) {
    let entry: ActiveDoc | undefined;
    if (index < pool.length) entry = pool[index];
    else if (index === pool.length && prewarm !== null) entry = prewarm;
    else entry = warm[index - pool.length - prewarmCount];
    if (
      entry === undefined ||
      (entry.mimeType === MIME_HTML && entry.fileId !== active?.fileId)
    )
      continue;
    if (mounted.some((existing) => existing.fileId === entry.fileId)) continue;
    mounted.push(entry);
    if (mounted.length >= MAX_MOUNTED) break;
  }

  useLayoutEffect(() => {
    if (!slot) return;
    let resizeFrame: number | undefined;
    const sync = () => {
      if (resizeFrame !== undefined) {
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = undefined;
      }
      const r = slot.getBoundingClientRect();
      const next = {
        height: r.height,
        left: r.left,
        top: r.top,
        width: r.width,
      };
      setRect((previous) =>
        previous?.height === next.height &&
        previous.left === next.left &&
        previous.top === next.top &&
        previous.width === next.width
          ? previous
          : next,
      );
    };
    const scheduleSync = () => {
      resizeFrame ??= window.requestAnimationFrame(sync);
    };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(slot);
    window.addEventListener("resize", scheduleSync);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", scheduleSync);
      if (resizeFrame !== undefined) window.cancelAnimationFrame(resizeFrame);
    };
  }, [slot]);

  const visible = active !== null && slot !== null && rect !== null;

  return (
    <div
      aria-hidden={!visible}
      className={cn("fixed z-10", !visible && "pointer-events-none invisible")}
      style={visible ? { ...rect } : { height: 0, left: 0, top: 0, width: 0 }}
    >
      {mounted.length > 0 ? (
        <PooledFrames active={active} mounted={mounted} />
      ) : null}
    </div>
  );
};

function PooledFrames({
  active,
  mounted,
}: {
  active: ActiveDoc | null;
  mounted: ActiveDoc[];
}) {
  const reducedMotion = useReducedMotion();
  const activeKey = active === null ? null : viewerKey(active);
  return mounted.map((entry) => (
    <div
      aria-hidden={viewerKey(entry) !== activeKey}
      className={cn(
        "absolute inset-0",
        entry.fileId !== active?.fileId && "hidden",
      )}
      key={entry.fileId}
    >
      <PooledIframe
        active={viewerKey(entry) === activeKey}
        documentMode={entry.documentMode ?? "edit"}
        fileId={entry.fileId}
        key={viewerKey(entry)}
        mimeType={entry.mimeType}
        reducedMotion={reducedMotion}
        title={entry.title}
        {...(entry.version === undefined ? {} : { version: entry.version })}
      />
    </div>
  ));
}

export function viewerKey(entry: ActiveDoc): string {
  const contentVersion =
    entry.mimeType === MIME_HTML ? (entry.version ?? "current") : "stable";
  return `${entry.fileId}:${entry.reloadKey}:${contentVersion}:${entry.documentMode ?? "edit"}`;
}

interface PooledIframeProps {
  active: boolean;
  documentMode: DocumentMode;
  fileId: string;
  mimeType: string;
  reducedMotion: boolean;
  title: string;
  version?: string;
}

const PooledIframe = ({
  active,
  documentMode,
  fileId,
  mimeType,
  reducedMotion,
  title,
  version,
}: PooledIframeProps) => {
  if (mimeType === MIME_HTML) {
    return (
      <HtmlIframe
        active={active}
        fileId={fileId}
        reducedMotion={reducedMotion}
        title={title}
        version={version}
      />
    );
  }
  return (
    <DrivePreviewIframe
      active={active}
      documentMode={documentMode}
      fileId={fileId}
      mimeType={mimeType}
      reducedMotion={reducedMotion}
      title={title}
    />
  );
};

const DrivePreviewIframe = ({
  active,
  documentMode,
  fileId,
  mimeType,
  reducedMotion,
  title,
}: PooledIframeProps) => {
  const src = embedUrlFor(mimeType, fileId, documentMode);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    markNav(fileId, "iframeMount");
  }, [fileId]);

  return (
    <DocumentFrame
      active={active}
      loading={isLoading}
      reducedMotion={reducedMotion}
    >
      <DriveDocumentIframe
        mimeType={mimeType}
        allow="clipboard-read; clipboard-write"
        ref={attachIframeLoadListener.bind(undefined, () => {
          markIframeLoaded(fileId, setIsLoading);
        })}
        src={src}
        title={title}
      />
    </DocumentFrame>
  );
};

function attachIframeLoadListener(
  onLoaded: () => void,
  iframe: HTMLIFrameElement | null,
) {
  if (!iframe) return;
  iframe.addEventListener("load", onLoaded);
  return () => {
    iframe.removeEventListener("load", onLoaded);
  };
}

function markIframeLoaded(
  fileId: string,
  setIsLoading: (isLoading: boolean) => void,
): void {
  markNav(fileId, "iframeLoad");
  setIsLoading(false);
}

function HtmlIframe({
  active,
  fileId,
  reducedMotion,
  title,
  version,
}: {
  active: boolean;
  fileId: string;
  reducedMotion: boolean;
  title: string;
  version: string | undefined;
}) {
  const [isLoading, setIsLoading] = useState(true);
  return (
    <DocumentFrame
      active={active}
      loading={isLoading}
      reducedMotion={reducedMotion}
    >
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

type LoadingOverlayPhase =
  | "exiting"
  | "hidden"
  | "masked-exiting"
  | "masked"
  | "visible";

type LoadingOverlayState =
  | { readonly kind: Exclude<LoadingOverlayPhase, "visible"> }
  | {
      readonly kind: "visible";
      readonly shownAt: number;
      readonly pace: "normal" | "slow";
    };

function DocumentFrame({
  active,
  children,
  loading,
  reducedMotion,
}: {
  active: boolean;
  children: React.ReactNode;
  loading: boolean;
  reducedMotion: boolean;
}) {
  const { phase, slow } = useDocumentLoading(active, loading, reducedMotion);
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
            <DocumentLoading
              label={slow ? "Still loading document…" : "Loading document…"}
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
  reducedMotion: boolean,
): { phase: LoadingOverlayPhase; slow: boolean } {
  const [state, setState] = useState<LoadingOverlayState>({ kind: "masked" });
  const generationRef = useRef(0);
  const phaseRef = useRef<LoadingOverlayState>(state);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const animationFrames: number[] = [];
    const timers: number[] = [];
    const isCurrent = () => generationRef.current === generation;
    const publish = (next: LoadingOverlayState) => {
      phaseRef.current = next;
      setState(next);
    };
    const cleanup = () => {
      if (generationRef.current === generation) generationRef.current += 1;
      for (const timer of timers) window.clearTimeout(timer);
      for (const frame of animationFrames) window.cancelAnimationFrame(frame);
    };

    if (!active) {
      publish({ kind: loading ? "masked" : "hidden" });
      return cleanup;
    }

    if (loading) {
      if (phaseRef.current.kind === "visible")
        publish({ ...phaseRef.current, pace: "normal" });
      if (
        phaseRef.current.kind === "exiting" ||
        phaseRef.current.kind === "hidden" ||
        phaseRef.current.kind === "masked-exiting"
      ) {
        publish({ kind: "masked" });
      }
      if (phaseRef.current.kind !== "visible") {
        timers.push(
          window.setTimeout(() => {
            if (!isCurrent()) return;
            publish({ kind: "visible", shownAt: Date.now(), pace: "normal" });
          }, DOCUMENT_LOADING_DELAY_MS),
        );
      }
      timers.push(
        window.setTimeout(() => {
          if (isCurrent() && phaseRef.current.kind === "visible")
            publish({ ...phaseRef.current, pace: "slow" });
        }, DOCUMENT_LOADING_SLOW_MS),
      );
      return cleanup;
    }

    if (phaseRef.current.kind === "visible")
      publish({ ...phaseRef.current, pace: "normal" });
    if (phaseRef.current.kind === "hidden") return cleanup;
    const minimumRemaining =
      phaseRef.current.kind === "visible"
        ? Math.max(
            0,
            phaseRef.current.shownAt + DOCUMENT_LOADING_MIN_MS - Date.now(),
          )
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
                  publish({ kind: "hidden" });
                  return;
                }
                publish({
                  kind:
                    phaseRef.current.kind === "visible"
                      ? "exiting"
                      : "masked-exiting",
                });
                timers.push(
                  window.setTimeout(() => {
                    if (isCurrent()) publish({ kind: "hidden" });
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

  return {
    phase: state.kind,
    slow: state.kind === "visible" && state.pace === "slow",
  };
}
