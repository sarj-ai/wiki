"use client";

import { useEffect, useState } from "react";

import { DocSkeleton } from "@/components/wiki-document/doc-skeleton";
import { type ActiveDoc, useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { API_URL } from "@/lib/env";
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

const MAX_POOL = 3;

const MAX_MOUNTED = 4;

export const HTML_IFRAME_SANDBOX = "allow-popups allow-popups-to-escape-sandbox allow-scripts";

export function htmlContentUrl(fileId: string, version?: string): string {
  const query = new URLSearchParams({ version: version ?? "current" });
  return `${API_URL}/files/${encodeURIComponent(fileId)}/content?${query.toString()}`;
}

export const DocViewerHost = () => {
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
      head.mimeType !== active.mimeType ||
      head.version !== active.version)
  ) {
    setPool([active, ...pool.filter((entry) => entry.fileId !== active.fileId)].slice(0, MAX_POOL));
  }

  const mounted: ActiveDoc[] = [];
  const seen = new Set<string>();
  for (const entry of [...pool, ...(prewarm ? [prewarm] : []), ...warm]) {
    if (entry.mimeType === MIME_HTML && entry.fileId !== active?.fileId) continue;
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

  return (
    <div
      aria-hidden={!visible}
      className={cn("fixed z-10", !visible && "pointer-events-none invisible")}
      style={visible ? { ...rect } : { height: 0, left: 0, top: 0, width: 0 }}
    >
      {mounted.map((entry) => (
        <div
          className={cn("absolute inset-0", entry.fileId !== active?.fileId && "hidden")}
          key={entry.fileId}
        >
          <PooledIframe
            fileId={entry.fileId}
            key={`${entry.fileId}:${entry.reloadKey}:${entry.version ?? "current"}`}
            mimeType={entry.mimeType}
            title={entry.title}
            {...(entry.version === undefined ? {} : { version: entry.version })}
          />
        </div>
      ))}
    </div>
  );
};

interface PooledIframeProps {
  fileId: string;
  mimeType: string;
  title: string;
  version?: string;
}

const PooledIframe = ({ fileId, mimeType, title, version }: PooledIframeProps) => {
  if (mimeType === MIME_HTML) {
    return isolatedHtmlIframe({ fileId, title, version });
  }
  return <DrivePreviewIframe fileId={fileId} mimeType={mimeType} title={title} />;
};

const DrivePreviewIframe = ({ fileId, mimeType, title }: PooledIframeProps) => {
  const src = embedUrlFor(mimeType, fileId);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    markNav(fileId, "iframeMount");
  }, [fileId]);

  const attachLoadListener = (iframe: HTMLIFrameElement | null) => {
    if (!iframe) return;
    const handleLoad = () => {
      markNav(fileId, "iframeLoad");
      setIsLoading(false);
    };
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
};

function isolatedHtmlIframe({
  fileId,
  title,
  version,
}: {
  fileId: string;
  title: string;
  version: string | undefined;
}) {
  return (
    <iframe
      className="block h-full w-full border-0"
      referrerPolicy="no-referrer"
      sandbox={HTML_IFRAME_SANDBOX}
      src={htmlContentUrl(fileId, version)}
      title={title}
    />
  );
}
