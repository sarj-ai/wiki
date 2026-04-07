"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Progress } from "@/components/ui/progress";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Upload, FileArchive, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import type { ImportJob } from "@/types";

interface ImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  parentId?: string | null;
  workspaceId?: string | null;
}

export function ImportDialog({ open, onOpenChange, parentId, workspaceId }: ImportDialogProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>(null);

  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  useEffect(() => {
    if (!open) {
      if (pollRef.current) clearInterval(pollRef.current);
      setFile(null);
      setJob(null);
      setIsUploading(false);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [open]);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  }, []);

  const handleUpload = useCallback(async () => {
    if (!file) return;
    setIsUploading(true);
    try {
      const result = await api.startImport(
        file,
        "notion",
        parentId ?? undefined,
        workspaceId ?? undefined,
      );
      setIsUploading(false);
      setJob(result);

      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getImportStatus(result.id);
          setJob(status);
          if (status.status === "completed" || status.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            queryClient.invalidateQueries({ queryKey: ["pages"] });
            if (status.status === "completed") {
              toast.success(`Imported ${status.processedItems} pages`);
            }
          }
        } catch {
          // keep polling
        }
      }, 2000);
    } catch {
      toast.error("Failed to start import");
      setIsUploading(false);
    }
  }, [file, parentId, workspaceId, queryClient]);

  const progress = job && job.totalItems > 0
    ? Math.round((job.processedItems / job.totalItems) * 100)
    : 0;

  const isDone = job?.status === "completed" || job?.status === "failed";
  const isFailed = job?.status === "failed";

  function formatSize(bytes: number) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px] gap-0 p-0">
        <DialogHeader className="px-6 pt-6 pb-4">
          <DialogTitle>Import pages</DialogTitle>
          <DialogDescription>
            Upload a Notion export (.zip) or a Markdown file (.md) to import pages.
          </DialogDescription>
        </DialogHeader>

        <Separator />

        <div className="px-6 py-5">
          {!job ? (
            <div className="flex flex-col gap-4">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="group flex items-center gap-4 rounded-lg border border-border p-4 transition-colors hover:bg-accent/50"
              >
                <div className="flex size-10 items-center justify-center rounded-lg bg-accent">
                  {file ? (
                    <FileArchive className="size-5 text-foreground" />
                  ) : (
                    <Upload className="size-5 text-muted-foreground group-hover:text-foreground transition-colors" />
                  )}
                </div>
                <div className="flex-1 text-left min-w-0">
                  {file ? (
                    <>
                      <p className="text-sm font-medium truncate">{file.name}</p>
                      <p className="text-xs text-muted-foreground">{formatSize(file.size)}</p>
                    </>
                  ) : (
                    <>
                      <p className="text-sm font-medium">Choose a file</p>
                      <p className="text-xs text-muted-foreground">Notion export (.zip) or Markdown (.md)</p>
                    </>
                  )}
                </div>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,.md,.html"
                onChange={handleFileChange}
                className="hidden"
              />
            </div>
          ) : isFailed ? (
            <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
              <AlertCircle className="size-5 text-destructive shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">Import failed</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {job.errorMessage ?? "An unknown error occurred."}
                </p>
              </div>
            </div>
          ) : job.status === "completed" ? (
            <div className="flex items-center gap-3 rounded-lg border border-green-500/30 bg-green-500/5 p-4">
              <CheckCircle2 className="size-5 text-green-500 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">
                  Imported {job.processedItems} {job.processedItems === 1 ? "page" : "pages"}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <Loader2 className="size-5 animate-spin text-primary shrink-0" />
                <p className="text-sm">
                  Importing... {job.processedItems} of {job.totalItems}
                </p>
              </div>
              <Progress value={progress} className="h-1.5" />
            </div>
          )}
        </div>

        <Separator />

        <DialogFooter className="px-6 py-4 sm:justify-between">
          {isFailed && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setJob(null);
                setFile(null);
              }}
            >
              Try again
            </Button>
          )}
          <Button
            size="sm"
            onClick={isDone ? () => onOpenChange(false) : handleUpload}
            disabled={!isDone && (!file || isUploading || !!job)}
          >
            {isUploading ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" />
                Uploading...
              </>
            ) : isDone ? (
              "Done"
            ) : (
              "Import"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
