"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { DocReadView, docHtmlQuery } from "@/components/wiki-document/doc-read-view";
import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { useDocViewerStore, type ActiveDoc } from "@/components/wiki-document/doc-viewer-store";
import { embedUrlFor } from "@/lib/file-urls";
import { markNav } from "@/lib/perf-marks";
import { cn } from "@/lib/utils";
import { MIME_DOC } from "@/types/wiki";

interface SlotRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const MAX_POOL = 3;

const MAX_MOUNTED = 4;

export function DocViewerHost() {
  const active = useDocViewerStore((s) => s.active);
  const prewarm = useDocViewerStore((s) => s.prewarm);
  const warm = useDocViewerStore((s) => s.warm);
  const slot = useDocViewerStore((s) => s.slot);
  const [pool, setPool] = useState<ActiveDoc[]>([]);
  const [rect, setRect] = useState<SlotRect | null>(null);

  const head = pool[0];
  if (
    active &&
    (head?.fileId !== active.fileId ||
      head.reloadKey !== active.reloadKey ||
      head.mimeType !== active.mimeType)
  ) {
    setPool([active, ...pool.filter((entry) => entry.fileId !== active.fileId)].slice(0, MAX_POOL));
  }

  const mounted: ActiveDoc[] = [];
  const seen = new Set<string>();
  for (const entry of [...pool, ...(prewarm ? [prewarm] : []), ...warm]) {
    if (seen.has(entry.fileId)) continue;
    seen.add(entry.fileId);
    mounted.push(entry);
    if (mounted.length >= MAX_MOUNTED) break;
  }
  const pooled = new Set(pool.map((entry) => entry.fileId));

  useEffect(() => {
    if (!slot) return;
    const sync = () => {
      const r = slot.getBoundingClientRect();
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };
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
      className={cn("fixed z-10", !visible && "pointer-events-none invisible")}
      style={visible ? { ...rect } : { top: 0, left: 0, width: 0, height: 0 }}
      aria-hidden={!visible}
    >
      {mounted.map((entry) => (
        <div
          key={entry.fileId}
          className={cn("absolute inset-0", entry.fileId !== active?.fileId && "hidden")}
        >
          <PooledIframe
            key={`${entry.fileId}:${entry.reloadKey}`}
            fileId={entry.fileId}
            mimeType={entry.mimeType}
            title={entry.title}
            warmOnly={!pooled.has(entry.fileId)}
            isActive={entry.fileId === active?.fileId}
          />
        </div>
      ))}
    </div>
  );
}

interface PooledIframeProps {
  fileId: string;
  mimeType: string;
  title: string;
  warmOnly: boolean;
  isActive: boolean;
}

function PooledIframe({ fileId, mimeType, title, warmOnly, isActive }: PooledIframeProps) {
  // Only the active doc reacts to toolbar edit mode; warm pool entries stay in view.
  const editing = useDocViewerStore((s) => s.editing);
  const editGeneration = useDocViewerStore((s) => s.editGeneration);
  const src = embedUrlFor(mimeType, fileId);
  // For non-doc embeds: skeleton until first load. For the Google editor: skeleton
  // until this editGeneration has loaded (re-enters cleanly after View → Edit).
  const [loadedGen, setLoadedGen] = useState<number | null>(null);
  const [embedLoaded, setEmbedLoaded] = useState(false);
  const isDoc = mimeType === MIME_DOC;
  const prefetchOnly = isDoc && warmOnly;
  const showEditor = isDoc && isActive && editing;
  const isLoading = showEditor ? loadedGen !== editGeneration : !embedLoaded;

  useEffect(() => {
    if (prefetchOnly) return; // no view mounted yet — don't skew the perf marks
    markNav(fileId, "iframeMount");
  }, [fileId, prefetchOnly]);

  // Warm/prewarm docs the user hasn't opened only prefetch the export — no
  // hidden iframe or layout work until the doc becomes active.
  if (prefetchOnly) {
    return <DocPrefetch fileId={fileId} />;
  }

  // Google Docs open in the themed read view; Edit (toolbar) swaps in the live
  // Google editor, View swaps back.
  if (isDoc && !showEditor) {
    return <DocReadView fileId={fileId} title={title} />;
  }

  return (
    <div className="relative h-full w-full">
      {isLoading && (
        <div
          className="bg-background absolute inset-0 z-10 overflow-hidden"
          aria-label="Loading document"
        >
          <DocSkeleton />
        </div>
      )}
      <iframe
        key={showEditor ? `edit-${editGeneration}` : `embed-${fileId}`}
        onLoad={() => {
          markNav(fileId, "iframeLoad");
          if (showEditor) setLoadedGen(editGeneration);
          else setEmbedLoaded(true);
        }}
        title={title}
        src={src}
        className="block h-full w-full border-0"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}

function DocPrefetch({ fileId }: { fileId: string }) {
  useQuery(docHtmlQuery(fileId));
  return null;
}
