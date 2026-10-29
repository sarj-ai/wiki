"use client";

import { FileWarning, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { DocumentLoading } from "@/components/wiki-document/document-loading";

import { demoAssetUrl, type DemoFile } from "./demo-data";
import { DEMO_PREVIEW_CLIENT } from "./demo-preview-client";

export function DemoPreview({ file, onReload }: { file: DemoFile; onReload: () => void }) {
  const [html, setHtml] = useState<null | string>(null);
  const [status, setStatus] = useState<"failed" | "loading" | "ready" | "slow">("loading");
  const timeout = useRef<null | number>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      controller.abort();
      setStatus("slow");
    }, 10_000);
    timeout.current = timer;
    (async () => {
      const content = await DEMO_PREVIEW_CLIENT.load(file.id, controller.signal);
      if (!controller.signal.aborted) setHtml(content);
    })().catch(() => {
      if (controller.signal.aborted) return;
      window.clearTimeout(timer);
      timeout.current = null;
      setStatus("failed");
    });
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [file.id]);

  const settled = (next: "failed" | "ready") => {
    if (next === "ready" && html === null) return;
    if (timeout.current !== null) window.clearTimeout(timeout.current);
    timeout.current = null;
    setStatus(next);
  };

  return (
    <section
      aria-busy={status === "loading"}
      aria-label={`${file.format} preview`}
      className="bg-muted/30 relative min-h-0 flex-1"
    >
      {html === null ? null : (
        <iframe
          aria-hidden={status !== "ready"}
          className="html-theme-frame block size-full border-0"
          onError={() => settled("failed")}
          onLoad={() => settled("ready")}
          referrerPolicy="no-referrer"
          sandbox=""
          srcDoc={html}
          tabIndex={status === "ready" ? 0 : -1}
          title={`${file.name} — read-only preview`}
        />
      )}
      {status === "loading" ? (
        <div className="bg-background absolute inset-0">
          <DocumentLoading label={`Opening ${file.name}…`} />
        </div>
      ) : null}
      {status === "slow" || status === "failed" ? (
        <div className="bg-background absolute inset-0 flex flex-col items-center justify-center gap-4 p-6 text-center">
          <FileWarning aria-hidden className="text-muted-foreground size-8" />
          <div>
            <h2 className="font-semibold">
              {status === "failed"
                ? "The preview couldn’t load"
                : "The preview is taking a little longer"}
            </h2>
            <p className="text-muted-foreground mt-1 max-w-sm text-sm">
              Try loading it again, or download the sample to open it on your device.
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-2">
            <Button onClick={onReload} variant="outline">
              <RefreshCw aria-hidden /> Try again
            </Button>
            <Button asChild>
              <a download={file.downloadFilename} href={demoAssetUrl(file.downloadFilename)}>
                Download sample
              </a>
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
