"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BrushCleaning,
  Copy,
  FileCheck2,
  FilePenLine,
  FileText,
  Loader2,
  Paintbrush,
  Sparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { FolderPickerDialog } from "@/components/wiki-sidebar/folder-picker-dialog";
import { ApiError } from "@/lib/api-error";
import { documentTransformApi } from "@/lib/document-transform-client";
import {
  type DocumentTransformMode,
  DocumentTransformNeedsReviewError,
  type DocumentTransformOutputStrategy,
  type DocumentTransformResult,
  type DocumentTransformSource,
} from "@/lib/document-transform-types";
import type { SmartDestination } from "@/lib/smart-create-types";
import { cn } from "@/lib/utils";
import { injectNewFileIntoCaches } from "@/lib/wiki-cache";
import { wikiKeys } from "@/lib/wiki-keys";

const MARKDOWN_LIMIT = 512 * 1024;

const MODES = [
  {
    description: "Clean the writing, then apply the complete Sarj document system.",
    icon: Sparkles,
    label: "Both",
    value: "both",
  },
  {
    description: "Apply Sarj typography, spacing, logo, and page styles.",
    icon: Paintbrush,
    label: "Formatting",
    value: "format",
  },
  {
    description: "Remove obvious AI phrasing while protecting facts, links, citations, and code.",
    icon: BrushCleaning,
    label: "Unslop",
    value: "unslop",
  },
] as const satisfies readonly {
  description: string;
  icon: typeof Sparkles;
  label: string;
  value: DocumentTransformMode;
}[];

export type DocumentPolishSource =
  | {
      allowInPlace: boolean;
      expectedModifiedTime: string;
      fileId: string;
      kind: "drive_file";
      name: string;
    }
  | {
      initialDestination: SmartDestination;
      kind: "markdown";
    };

interface DocumentPolishDialogProps {
  onInPlaceComplete?: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  source: DocumentPolishSource;
}

export function DocumentPolishDialog({
  onInPlaceComplete,
  onOpenChange,
  open,
  source,
}: DocumentPolishDialogProps) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [mode, setMode] = useState<DocumentTransformMode>("both");
  const [outputStrategy, setOutputStrategy] = useState<DocumentTransformOutputStrategy>("copy");
  const [markdown, setMarkdown] = useState("");
  const [name, setName] = useState("Untitled document");
  const [destination, setDestination] = useState<null | SmartDestination>(
    source.kind === "markdown" ? source.initialDestination : null,
  );
  const [destinationOpen, setDestinationOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stage, setStage] = useState<"idle" | "starting" | "transforming">("idle");
  const [reviewMessage, setReviewMessage] = useState<null | string>(null);
  const [reviewResult, setReviewResult] = useState<DocumentTransformResult | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const idempotencyKey = useRef<null | string>(null);
  const submitLatch = useRef(false);
  const capabilities = useQuery({
    enabled: open && source.kind === "drive_file" && source.allowInPlace,
    queryFn: documentTransformApi.capabilities,
    queryKey: ["document-transform", "capabilities"],
    staleTime: 60_000,
  });
  const effectiveOutputStrategy: DocumentTransformOutputStrategy =
    source.kind === "drive_file" && source.allowInPlace && capabilities.data?.inPlace === true
      ? outputStrategy
      : "copy";

  const resetRequest = () => {
    idempotencyKey.current = null;
    setReviewMessage(null);
    setReviewResult(null);
  };

  const close = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    submitLatch.current = false;
    idempotencyKey.current = null;
    setMode("both");
    setOutputStrategy("copy");
    setMarkdown("");
    setName("Untitled document");
    setDestination(source.kind === "markdown" ? source.initialDestination : null);
    setDestinationOpen(false);
    setSubmitting(false);
    setStage("idle");
    setReviewMessage(null);
    setReviewResult(null);
    onOpenChange(false);
  };

  const inputSource = (): DocumentTransformSource | null => {
    if (source.kind === "drive_file") {
      return {
        expectedModifiedTime: source.expectedModifiedTime,
        fileId: source.fileId,
        kind: source.kind,
        name: source.name,
      };
    }
    if (!destination || !name.trim() || !markdown.trim()) return null;
    return {
      content: markdown,
      destinationFolderId: destination.folderId,
      kind: "markdown",
      name: name.trim(),
    };
  };

  const create = async () => {
    const transformSource = inputSource();
    if (!transformSource || submitLatch.current) return;
    submitLatch.current = true;
    setSubmitting(true);
    setStage("starting");
    setReviewMessage(null);
    setReviewResult(null);
    idempotencyKey.current ??= crypto.randomUUID();
    const controller = new AbortController();
    const finishSubmission = () => {
      if (abortRef.current !== controller) return;
      abortRef.current = null;
      submitLatch.current = false;
      setSubmitting(false);
      setStage("idle");
    };
    abortRef.current?.abort();
    abortRef.current = controller;
    try {
      const task = await documentTransformApi.create(
        transformSource,
        mode,
        idempotencyKey.current,
        effectiveOutputStrategy,
      );
      controller.signal.throwIfAborted();
      setStage("transforming");
      const result = await documentTransformApi.waitForCompletion(task, controller.signal);
      if (result.outputStrategy === "in_place") {
        await queryClient.invalidateQueries({ queryKey: wikiKeys.file(result.outputFile.id) });
        onInPlaceComplete?.();
        toast.success(result.changed ? `Updated “${result.outputFile.name}”` : "No changes needed");
      } else {
        injectNewFileIntoCaches(queryClient, result.outputFile);
        toast.success(`Created “${result.outputFile.name}”`);
      }
      close();
      router.push(`/d/${encodeURIComponent(result.outputFile.id)}`);
    } catch (error: unknown) {
      if (error instanceof DocumentTransformNeedsReviewError) {
        setReviewMessage(error.message);
        setReviewResult(error.result ?? null);
        if (error.result?.outputStrategy === "in_place") {
          await queryClient.invalidateQueries({
            queryKey: wikiKeys.file(error.result.outputFile.id),
          });
          onInPlaceComplete?.();
        } else if (error.result) {
          injectNewFileIntoCaches(queryClient, error.result.outputFile);
        }
        toast.warning("The document needs review");
      } else if (!(error instanceof DOMException && error.name === "AbortError")) {
        toast.error(errorMessage(error, "Couldn’t polish document"));
      }
    } finally {
      finishSubmission();
    }
  };

  const valid = inputSource() !== null;
  const destinationLabel = destination
    ? destination.folderPath.length > 0
      ? destination.folderPath.join(" / ")
      : destination.driveName
    : "Choose a folder";

  return (
    <>
      <Dialog
        onOpenChange={(next) => {
          if (!next && !submitting) close();
        }}
        open={open}
      >
        <DialogContent className="max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-xl">
          <div className="border-b px-5 pt-5 pb-4 sm:px-6 sm:pt-6">
            <DialogHeader>
              <DialogTitle>Polish document</DialogTitle>
              <DialogDescription>
                Choose how Sarj Wiki should refine this document.
              </DialogDescription>
            </DialogHeader>
          </div>

          <div className="space-y-5 overflow-y-auto p-5 sm:px-6">
            {source.kind === "markdown" ? (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="polish-markdown-name">Document name</Label>
                  <Input
                    disabled={submitting}
                    id="polish-markdown-name"
                    maxLength={255}
                    onChange={(event) => {
                      setName(event.target.value);
                      resetRequest();
                    }}
                    value={name}
                  />
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-baseline justify-between gap-3">
                    <Label htmlFor="polish-markdown-content">Markdown</Label>
                    <span className="text-muted-foreground text-xs tabular-nums">
                      {markdown.length.toLocaleString()} / {MARKDOWN_LIMIT.toLocaleString()}
                    </span>
                  </div>
                  <Textarea
                    className="min-h-44 resize-y font-mono text-xs leading-relaxed"
                    disabled={submitting}
                    id="polish-markdown-content"
                    maxLength={MARKDOWN_LIMIT}
                    onChange={(event) => {
                      setMarkdown(event.target.value);
                      resetRequest();
                    }}
                    placeholder={"# Proposal\n\nPaste Markdown here…"}
                    value={markdown}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Destination</Label>
                  <Button
                    className="h-auto w-full justify-between px-3 py-2 text-left"
                    disabled={submitting}
                    onClick={() => {
                      setDestinationOpen(true);
                    }}
                    type="button"
                    variant="outline"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {destination?.driveName ?? "No workspace selected"}
                      </span>
                      <span className="text-muted-foreground block truncate text-xs">
                        {destinationLabel}
                      </span>
                    </span>
                    <span className="text-muted-foreground shrink-0 text-xs">Change</span>
                  </Button>
                </div>
              </div>
            ) : null}

            <fieldset className="space-y-2">
              <legend className="mb-2 text-sm font-medium">Polish method</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {MODES.map((option) => {
                  const Icon = option.icon;
                  const selected = mode === option.value;
                  return (
                    <button
                      aria-pressed={selected}
                      className={cn(
                        "focus-visible:ring-ring/50 relative rounded-lg border p-3 text-left transition-[border-color,background-color,box-shadow] outline-none focus-visible:ring-[3px] disabled:opacity-50",
                        selected
                          ? "border-primary bg-primary/5 shadow-sm"
                          : "border-border hover:bg-muted/50",
                      )}
                      disabled={submitting}
                      key={option.value}
                      onClick={() => {
                        setMode(option.value);
                        resetRequest();
                      }}
                      type="button"
                    >
                      <span className="flex items-center gap-2">
                        <Icon className={cn("size-4", selected && "text-primary")} />
                        <span className="text-sm font-semibold">{option.label}</span>
                      </span>
                      <span className="text-muted-foreground mt-2 block text-xs leading-relaxed">
                        {option.description}
                      </span>
                      {option.value === "both" ? (
                        <span className="text-primary mt-2 block text-[10px] font-semibold tracking-wide uppercase">
                          Recommended
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </fieldset>

            {source.kind === "drive_file" &&
            source.allowInPlace &&
            capabilities.data?.inPlace === true ? (
              <fieldset className="space-y-2">
                <legend className="mb-2 text-sm font-medium">Save result</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  <button
                    aria-pressed={outputStrategy === "copy"}
                    className={cn(
                      "focus-visible:ring-ring/50 rounded-lg border p-3 text-left outline-none focus-visible:ring-[3px] disabled:opacity-50",
                      outputStrategy === "copy"
                        ? "border-primary bg-primary/5 shadow-sm"
                        : "border-border hover:bg-muted/50",
                    )}
                    disabled={submitting}
                    onClick={() => {
                      setOutputStrategy("copy");
                      resetRequest();
                    }}
                    type="button"
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold">
                      <Copy className="size-4" />
                      Create review copy
                    </span>
                    <span className="text-muted-foreground mt-2 block text-xs leading-relaxed">
                      Keep the original untouched and create a sibling Google Doc.
                    </span>
                    <span className="text-primary mt-2 block text-[10px] font-semibold tracking-wide uppercase">
                      Recommended
                    </span>
                  </button>
                  <button
                    aria-pressed={outputStrategy === "in_place"}
                    className={cn(
                      "focus-visible:ring-ring/50 rounded-lg border p-3 text-left outline-none focus-visible:ring-[3px] disabled:opacity-50",
                      outputStrategy === "in_place"
                        ? "border-primary bg-primary/5 shadow-sm"
                        : "border-border hover:bg-muted/50",
                    )}
                    disabled={submitting}
                    onClick={() => {
                      setOutputStrategy("in_place");
                      resetRequest();
                    }}
                    type="button"
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold">
                      <FilePenLine className="size-4" />
                      Edit original
                    </span>
                    <span className="text-muted-foreground mt-2 block text-xs leading-relaxed">
                      Amend this Google Doc; Drive version history remains available.
                    </span>
                  </button>
                </div>
              </fieldset>
            ) : null}

            <div className="bg-muted/45 flex items-center gap-3 rounded-lg border p-3">
              <span aria-hidden className="flex shrink-0 items-center gap-1.5">
                <FileText className="text-muted-foreground size-4" />
                <ArrowRight className="text-muted-foreground size-3.5" />
                <FileCheck2 className="text-primary size-4" />
              </span>
              <p className="text-muted-foreground text-xs leading-relaxed">
                {source.kind === "drive_file"
                  ? effectiveOutputStrategy === "in_place"
                    ? `“${source.name}” will be amended only after revision and safety checks pass.`
                    : `“${source.name}” stays unchanged. A new Google Doc will be created next to it.`
                  : "Your Markdown stays unchanged. A new Google Doc will be created in the selected folder."}
              </p>
            </div>

            {submitting ? (
              <div
                aria-live="polite"
                className="border-primary/20 bg-primary/5 flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm"
              >
                <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
                <span>
                  {stage === "starting"
                    ? "Starting document polish…"
                    : "Creating and checking the new document… Keep this window open."}
                </span>
              </div>
            ) : null}

            {reviewMessage ? (
              <div
                className="border-border bg-muted/50 rounded-lg border px-3 py-2.5 text-sm"
                role="alert"
              >
                <p className="font-medium">This document needs review</p>
                <p className="text-muted-foreground mt-1 text-xs">{reviewMessage}</p>
                {reviewResult ? (
                  <Button
                    className="mt-2 px-0"
                    onClick={() => {
                      const fileId = reviewResult.outputFile.id;
                      close();
                      router.push(`/d/${encodeURIComponent(fileId)}`);
                    }}
                    size="xs"
                    type="button"
                    variant="link"
                  >
                    {reviewResult.outputStrategy === "in_place"
                      ? "Open original document"
                      : "Open review copy"}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>

          <DialogFooter className="bg-background grid grid-cols-2 border-t px-5 py-4 sm:flex sm:px-6">
            <Button disabled={submitting} onClick={close} type="button" variant="ghost">
              Cancel
            </Button>
            <Button disabled={submitting || !valid} onClick={() => void create()} type="button">
              {submitting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              {submitting
                ? "Polishing…"
                : effectiveOutputStrategy === "in_place"
                  ? "Polish original"
                  : "Create polished copy"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {destinationOpen ? (
        <FolderPickerDialog
          onOpenChange={setDestinationOpen}
          onSelect={(next) => {
            setDestination(next);
            resetRequest();
          }}
          open
        />
      ) : null}
    </>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}
