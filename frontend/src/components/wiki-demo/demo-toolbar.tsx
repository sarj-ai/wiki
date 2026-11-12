"use client";

import { Download } from "lucide-react";
import type { Ref } from "react";

import { Button } from "@/components/ui/button";
import { DocumentToolbar } from "@/components/wiki-document/document-toolbar";
import type { DocumentMode } from "@/lib/file-urls";

import { DEMO_WORKSPACE_NAME, type DemoFile, demoHref } from "./demo-data";
import { demoDownloadUrl, demoViewUrl } from "./demo-file-urls";

export function DemoToolbar({
  documentMode,
  file,
  onDocumentModeChange,
  onReload,
  reloadButtonRef,
}: {
  documentMode: DocumentMode;
  file: DemoFile;
  onDocumentModeChange: (mode: DocumentMode) => void;
  onReload: () => void;
  reloadButtonRef?: Ref<HTMLButtonElement>;
}) {
  const downloadUrl = demoDownloadUrl(file);
  return (
    <DocumentToolbar
      breadcrumb={[{ id: "demo-workspace", name: DEMO_WORKSPACE_NAME }]}
      copyHref={demoHref(file)}
      documentMode={documentMode}
      editorViewEnabled
      fileId={file.id}
      mimeType={file.mimeType}
      onDocumentModeChange={onDocumentModeChange}
      onReload={onReload}
      openUrl={demoViewUrl(file)}
      reloadButtonRef={reloadButtonRef}
      showDocumentTitle
      title={file.name}
    >
      {downloadUrl === null ? null : (
        <Button asChild size="xs" variant="outline">
          <a
            aria-label={`Download ${file.name}`}
            download={file.downloadFilename}
            href={downloadUrl}
            referrerPolicy="no-referrer"
            rel="noopener noreferrer"
            target="_blank"
          >
            <Download aria-hidden className="size-3.5" />
            <span className="hidden @min-[40rem]/toolbar:inline">Download</span>
          </a>
        </Button>
      )}
    </DocumentToolbar>
  );
}
