"use client";

import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";
import { displayFileName } from "@/lib/file-icons";
import { type DocumentMode, openUrlFor } from "@/lib/file-urls";
import type { BreadcrumbItem } from "@/types/wiki";
import {
  MIME_DOC,
  MIME_DOCX,
  MIME_MARKDOWN,
  MIME_PPTX,
  MIME_SLIDE,
} from "@/types/wiki";

import { DocumentToolbar } from "./document-toolbar";
import { LazyDocumentPolishDialog } from "./lazy-document-polish-dialog";
import { ReadOnlyShortLink } from "./read-only-short-link";
import { ReorganizeDocument } from "./reorganize-document";
import { ShortLinkDialog } from "./short-link-dialog";

interface WikiToolbarProps {
  authority?: "pending" | "resolved";
  breadcrumb: BreadcrumbItem[];
  canEdit: boolean;
  documentMode: DocumentMode;
  driveId: string | undefined;
  fileId: string;
  mimeType: string;
  modifiedTime?: string;
  onDocumentModeChange: (mode: DocumentMode) => void;
  onReload: () => void;
  parentFolderId: string | undefined;
  refreshModifiedTime?: () => Promise<string>;

  title: string;
}

export function WikiToolbar({
  authority = "resolved",
  breadcrumb,
  canEdit,
  documentMode,
  driveId,
  fileId,
  mimeType,
  modifiedTime,
  onDocumentModeChange,
  onReload,
  parentFolderId,
  refreshModifiedTime,
  title,
}: WikiToolbarProps) {
  const polishDialog = useDeferredDialog();
  const displayTitle = displayFileName(title, mimeType);

  return (
    <DocumentToolbar
      breadcrumb={breadcrumb}
      documentMode={documentMode}
      editorViewEnabled={canEdit}
      fileId={fileId}
      mimeType={mimeType}
      onDocumentModeChange={onDocumentModeChange}
      onReload={onReload}
      openUrl={openUrlFor(mimeType, fileId)}
      showModeSwitch={authority === "resolved"}
      title={displayTitle}
    >
      {authority === "resolved" &&
      modifiedTime !== undefined &&
      (mimeType === MIME_DOC ||
        mimeType === MIME_DOCX ||
        mimeType === MIME_MARKDOWN ||
        mimeType === MIME_SLIDE ||
        mimeType === MIME_PPTX) ? (
        <>
          <Button
            aria-label={
              mimeType === MIME_SLIDE || mimeType === MIME_PPTX
                ? "Polish presentation"
                : "Polish document"
            }
            onClick={() => {
              polishDialog.onOpenChange(true);
            }}
            size="xs"
            type="button"
            variant="outline"
          >
            <Sparkles className="size-3.5" />
            <span className="hidden @min-[40rem]/toolbar:inline">Polish</span>
          </Button>
          {polishDialog.mounted ? (
            <LazyDocumentPolishDialog
              key={fileId}
              onInPlaceComplete={onReload}
              onOpenChange={polishDialog.onOpenChange}
              open={polishDialog.open}
              source={{
                allowInPlace: canEdit && mimeType === MIME_DOC,
                expectedModifiedTime: modifiedTime,
                fileId,
                kind: "drive_file",
                mimeType,
                name: displayTitle,
                refreshExpectedModifiedTime:
                  refreshModifiedTime ??
                  (() =>
                    Promise.reject(
                      new Error(
                        "Couldn’t verify the document’s latest revision. Please reload.",
                      ),
                    )),
              }}
            />
          ) : null}
        </>
      ) : null}
      {authority === "resolved" ? (
        canEdit ? (
          <div className="size-6 shrink-0 @min-[40rem]/toolbar:w-24 pointer-coarse:min-h-11 pointer-coarse:min-w-11">
            <ReorganizeDocument
              driveId={driveId}
              fileId={fileId}
              fileName={displayTitle}
              parentFolderId={parentFolderId}
            />
          </div>
        ) : null
      ) : (
        <div className="size-6 shrink-0 @min-[40rem]/toolbar:w-24 pointer-coarse:min-h-11 pointer-coarse:min-w-11">
          <span
            aria-hidden
            className="bg-muted block size-full animate-pulse rounded-md"
          />
        </div>
      )}
      <div className="size-6 shrink-0 @min-[40rem]/toolbar:w-24 pointer-coarse:min-h-11 pointer-coarse:min-w-11">
        {authority === "resolved" ? (
          canEdit ? (
            <ShortLinkDialog fileId={fileId} />
          ) : (
            <ReadOnlyShortLink fileId={fileId} />
          )
        ) : (
          <span
            aria-hidden
            className="bg-muted block size-full animate-pulse rounded-md"
          />
        )}
      </div>
    </DocumentToolbar>
  );
}
