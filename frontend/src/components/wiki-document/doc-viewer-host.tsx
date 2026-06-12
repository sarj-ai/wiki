"use client";

import { useEffect, useState } from "react";

import { useDocViewerStore, type ActiveDoc } from "@/components/wiki-document/doc-viewer-store";
import { FileLoadingIndicator } from "@/components/wiki-document/file-loading-indicator";
import { embedUrlFor } from "@/lib/file-urls";
import { cn } from "@/lib/utils";

interface SlotRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const MAX_POOL = 3;

export function DocViewerHost() {
  const active = useDocViewerStore((s) => s.active);
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
      {pool.map((entry) => (
        <div
          key={entry.fileId}
          className={cn("absolute inset-0", entry.fileId !== active?.fileId && "hidden")}
        >
          <PooledIframe
            key={`${entry.fileId}:${entry.reloadKey}`}
            fileId={entry.fileId}
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

  const attachLoadListener = (iframe: HTMLIFrameElement | null) => {
    if (!iframe) return;
    const handleLoad = () => {
      setIsLoading(false);
    };
    iframe.addEventListener("load", handleLoad);
    return () => {
      iframe.removeEventListener("load", handleLoad);
    };
  };

  return (
    <div className="relative h-full w-full">
      {isLoading && (
        <div
          className="bg-background absolute inset-0 z-10 flex items-center justify-center"
          aria-label="Loading document"
        >
          <FileLoadingIndicator />
        </div>
      )}
      <iframe
        ref={attachLoadListener}
        title={title}
        src={src}
        className="block h-full w-full border-0"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
}
