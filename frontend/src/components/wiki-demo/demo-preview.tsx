"use client";

import { FileWarning, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { DocumentLoading } from "@/components/wiki-document/document-loading";

import type { DemoFile } from "./demo-data";
import { demoDownloadUrl, demoPreviewUrl } from "./demo-file-urls";

interface DemoPreviewProps {
  file: DemoFile;
  onReload: () => void;
}

export function DemoPreview({ file, onReload }: DemoPreviewProps) {
  return (
    <PreviewFrame
      file={file}
      key={`${file.id}:${file.previewUrl}`}
      onReload={onReload}
    />
  );
}

function PreviewFrame({ file, onReload }: DemoPreviewProps) {
  const [loadStatus, setLoadStatus] = useState<"loading" | "ready" | "slow">(
    "loading",
  );
  const timeout = useRef<null | number>(null);
  const previewUrl = demoPreviewUrl(file);
  const invalidPreview = previewUrl === null;
  const status = invalidPreview ? "failed" : loadStatus;
  useEffect(() => {
    if (invalidPreview) return;
    const timer = window.setTimeout(() => {
      setLoadStatus("slow");
    }, 10_000);
    timeout.current = timer;
    return () => {
      window.clearTimeout(timer);
    };
  }, [invalidPreview]);

  const loaded = () => {
    if (status !== "loading" || previewUrl === null) return;
    if (timeout.current !== null) window.clearTimeout(timeout.current);
    timeout.current = null;
    setLoadStatus("ready");
  };

  return (
    <section
      aria-busy={status === "loading"}
      aria-label={`${file.format} preview`}
      className="bg-muted/30 relative min-h-0 flex-1"
    >
      {status === "failed" ||
      status === "slow" ||
      previewUrl === null ? null : (
        <iframe
          aria-hidden={status !== "ready"}
          className="html-theme-frame block size-full border-0"
          onLoad={loaded}
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin"
          src={previewUrl}
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
        <PreviewFallback file={file} onReload={onReload} status={status} />
      ) : null}
    </section>
  );
}

interface PreviewFallbackProps extends DemoPreviewProps {
  status: "failed" | "slow";
}

function PreviewFallback({ file, onReload, status }: PreviewFallbackProps) {
  const downloadUrl = demoDownloadUrl(file);
  return (
    <div className="bg-background absolute inset-0 flex flex-col items-center justify-center gap-4 p-6 text-center">
      <FileWarning aria-hidden className="text-muted-foreground size-8" />
      <div>
        <h2 className="font-semibold">
          {status === "failed"
            ? "The preview couldn’t load"
            : "The preview is taking a little longer"}
        </h2>
        <p className="text-muted-foreground mt-1 max-w-sm text-sm">
          {downloadUrl === null
            ? "Try loading it again."
            : "Try loading it again, or download the sample to open it on your device."}
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        <Button onClick={onReload} variant="outline">
          <RefreshCw aria-hidden /> Try again
        </Button>
        {downloadUrl === null ? null : (
          <Button asChild>
            <a
              download={file.downloadFilename}
              href={downloadUrl}
              referrerPolicy="no-referrer"
              rel="noopener noreferrer"
              target="_blank"
            >
              Download sample
            </a>
          </Button>
        )}
      </div>
    </div>
  );
}
