"use client";

import { useEffect, useState } from "react";

import type { ActiveDoc } from "@/components/wiki-document/active-doc";
import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { cssVars } from "@/lib/css-vars";
import { embedUrlFor } from "@/lib/file-urls";
import { markNav } from "@/lib/mark-nav";
import { cn } from "@/lib/utils";

interface SlotRect {
  height: number;
  left: number;
  top: number;
  width: number;
}

const MAX_POOL = 3;

const MAX_MOUNTED = 4;

export function DocViewerHost() {
  const active = useDocViewerStore((s) => s.active);
  const prewarm = useDocViewerStore((s) => s.prewarm);
  const warm = useDocViewerStore((s) => s.warm);
  const slot = useDocViewerStore((s) => s.slot);
  const [pool, setPool] = useState<ActiveDoc[]>([]);
  const [rect, setRect] = useState<null | SlotRect>(null);

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

  useEffect(() => {
    if (!slot) return;
    const sync = () => {
      const r = slot.getBoundingClientRect();
      setRect({ height: r.height, left: r.left, top: r.top, width: r.width });
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
  const geometry = visible ? rect : { height: 0, left: 0, top: 0, width: 0 };

  return (
    <div
      aria-hidden={!visible}
      className={cn(
        "fixed top-(--doc-top) left-(--doc-left) z-10 h-(--doc-height) w-(--doc-width)",
        !visible && "pointer-events-none invisible",
      )}
      ref={cssVars({
        "--doc-height": `${geometry.height}px`,
        "--doc-left": `${geometry.left}px`,
        "--doc-top": `${geometry.top}px`,
        "--doc-width": `${geometry.width}px`,
      })}
    >
      {mounted.map((entry) => (
        <div
          className={cn("absolute inset-0", entry.fileId !== active?.fileId && "hidden")}
          key={entry.fileId}
        >
          <PooledIframe
            fileId={entry.fileId}
            key={`${entry.fileId}:${entry.reloadKey}`}
            mimeType={entry.mimeType}
            title={entry.title}
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
}

function PooledIframe({ fileId, mimeType, title }: PooledIframeProps) {
  const src = embedUrlFor(mimeType, fileId);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    markNav(fileId, "iframeMount");
  }, [fileId]);

  const handleLoad = () => {
    markNav(fileId, "iframeLoad");
    setIsLoading(false);
  };
  const attachLoadListener = (iframe: HTMLIFrameElement | null) => {
    if (!iframe) return;
    iframe.addEventListener("load", handleLoad);
    return () => {
      iframe.removeEventListener("load", handleLoad);
    };
  };

  return (
    <div className="relative h-full w-full">
      {isLoading ? (
        <div
          aria-label="Loading document"
          className="bg-background absolute inset-0 z-10 overflow-hidden"
        >
          <DocSkeleton />
        </div>
      ) : null}
      <iframe
        allow="clipboard-read; clipboard-write"
        className="block h-full w-full border-0"
        ref={attachLoadListener}
        src={src}
        title={title}
      />
    </div>
  );
}
