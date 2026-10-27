"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BrushCleaning,
  Copy,
  FileCheck2,
  FilePenLine,
  FileText,
  FileUp,
  Loader2,
  Paintbrush,
  Sparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { ensureDocumentAliasAction } from "@/app/actions/wiki-actions";
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
import { documentAliasKey } from "@/hooks/use-document-alias";
import { ApiError } from "@/lib/api-error";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { DOCUMENT_TRANSFORM_API } from "@/lib/document-transform-api";
import {
  claimTransformRecovery,
  completeTransformRecovery,
  readTransformRecovery,
  recordTransformTask,
  type TransformRecovery,
  writeTransformRecovery,
} from "@/lib/document-transform-recovery";
import {
  type DocumentTransformMode,
  DocumentTransformNeedsReviewError,
  type DocumentTransformOutputStrategy,
  type DocumentTransformResult,
  type DocumentTransformSource,
  DocumentTransformTerminalError,
} from "@/lib/document-transform-types";
import {
  MARKDOWN_LIMIT_BYTES,
  markdownByteLength,
  markdownTextError,
  readMarkdownFile,
} from "@/lib/markdown-import";
import {
  clearMarkdownRecovery,
  type MarkdownRecoveryBundle,
} from "@/lib/markdown-transform-recovery";
import { preservedInPlaceOutcome } from "@/lib/preserved-in-place-outcome";
import type { SmartDestination } from "@/lib/smart-create-types";
import { cn } from "@/lib/utils";
import { injectNewFileIntoCaches } from "@/lib/wiki-cache";
import { wikiKeys } from "@/lib/wiki-keys";
import { MIME_DOCX, MIME_PPTX, MIME_SLIDE } from "@/types/wiki";

import { DocumentPolishReceipt } from "./document-polish-receipt";
import { DocumentPolishRecovery } from "./document-polish-recovery";

const MODES = [
  {
    description: "Clean the writing, then apply the complete Sarj brand system.",
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
      mimeType: string;
      name: string;
      refreshExpectedModifiedTime: () => Promise<string>;
    }
  | {
      initialDestination: SmartDestination;
      kind: "markdown";
    };

export interface DocumentPolishDialogProps {
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
  const owner = useBrowserUserId();
  const saveDescriptionId = useId();
  const router = useRouter();
  const [mode, setMode] = useState<DocumentTransformMode>("both");
  const [outputStrategy, setOutputStrategy] = useState<DocumentTransformOutputStrategy>("copy");
  const [markdown, setMarkdown] = useState("");
  const [uploadedMarkdownId, setUploadedMarkdownId] = useState<null | string>(null);
  const [restoredDraft, setRestoredDraft] = useState<{
    owner: string;
    key: string;
    source: MarkdownRecoveryBundle["source"];
  } | null>(null);
  const [markdownError, setMarkdownError] = useState<null | string>(null);
  const [loadingMarkdownFile, setLoadingMarkdownFile] = useState(false);
  const [name, setName] = useState("Untitled document");
  const [copyName, setCopyName] = useState("");
  const [completedResult, setCompletedResult] = useState<DocumentTransformResult | null>(null);
  const [destination, setDestination] = useState<null | SmartDestination>(
    source.kind === "markdown" ? source.initialDestination : null,
  );
  const [destinationOpen, setDestinationOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stage, setStage] = useState<"idle" | "starting" | "transforming">("idle");
  const [terminalMessage, setTerminalMessage] = useState<null | string>(null);
  const [terminalTaskId, setTerminalTaskId] = useState<null | string>(null);
  const [retryMessage, setRetryMessage] = useState<null | string>(null);
  const [unverifiedOutputId, setUnverifiedOutputId] = useState<null | string>(null);
  const [unverifiedOutputIsOriginal, setUnverifiedOutputIsOriginal] = useState(false);
  const [reviewMessage, setReviewMessage] = useState<null | string>(null);
  const [reviewResult, setReviewResult] = useState<DocumentTransformResult | null>(null);
  const isPresentation =
    source.kind === "drive_file" &&
    (source.mimeType === MIME_SLIDE || source.mimeType === MIME_PPTX);
  const isWord = source.kind === "drive_file" && source.mimeType === MIME_DOCX;
  const requestedMode = isWord ? "format" : mode;
  const artifactNoun = isPresentation ? "presentation" : "document";
  const outputNoun = isPresentation
    ? "Google Slides presentation"
    : isWord
      ? "Word document (.docx)"
      : "Google Doc";
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const fileLoadGeneration = useRef(0);
  const fileLoadPending = useRef(false);
  const idempotencyKey = useRef<null | string>(null);
  const refreshedDriveSource = useRef<Extract<
    DocumentTransformSource,
    { kind: "drive_file" }
  > | null>(null);
  const lifecycleGeneration = useRef(0);
  const submitLatch = useRef(false);
  const outcomeHeadingRef = useRef<HTMLHeadingElement>(null);
  const scrollBodyRef = useRef<HTMLDivElement>(null);
  const revealedOutcome = useRef<null | string>(null);
  const outcomeIdentity = completedResult
    ? `complete:${completedResult.operationId}`
    : terminalMessage
      ? `failed:${terminalTaskId}:${terminalMessage}`
      : reviewMessage
        ? `review:${terminalTaskId}:${reviewMessage}`
        : null;

  useEffect(() => {
    if (!outcomeIdentity) {
      revealedOutcome.current = null;
      return;
    }
    if (!open || submitting || revealedOutcome.current === outcomeIdentity) return;
    const heading = outcomeHeadingRef.current;
    const body = scrollBodyRef.current;
    if (!heading || !body) return;
    revealedOutcome.current = outcomeIdentity;
    heading.focus({ preventScroll: true });
    body.scrollTo({ top: 0, behavior: "instant" });
  }, [open, submitting, outcomeIdentity]);
  const capabilities = useQuery({
    enabled: open && source.kind === "drive_file" && source.allowInPlace && !isWord,
    queryFn: async ({ signal }) => await DOCUMENT_TRANSFORM_API.capabilities(signal),
    queryKey: ["document-transform", "capabilities"],
    staleTime: 60_000,
  });
  const effectiveOutputStrategy: DocumentTransformOutputStrategy =
    source.kind === "drive_file" &&
    source.allowInPlace &&
    !isWord &&
    capabilities.data?.inPlace === true
      ? outputStrategy
      : "copy";
  const capabilityPending =
    source.kind === "drive_file" && source.allowInPlace && !isWord && capabilities.isPending;
  const capabilityFailed =
    source.kind === "drive_file" && source.allowInPlace && !isWord && capabilities.isError;
  const sourceIdentity = source.kind === "drive_file" ? `drive:${source.fileId}` : "markdown";
  const recoveryKey = ["document-transform-recovery", owner, sourceIdentity] as const;
  const recovery = useQuery({
    enabled: open,
    initialData: owner ? undefined : null,
    queryKey: recoveryKey,
    queryFn: () => readTransformRecovery(owner, sourceIdentity),
    retry: false,
    staleTime: Infinity,
  });
  const unresolved = recovery.data ?? null;
  const currentPolish = useQuery({
    enabled:
      open &&
      owner !== null &&
      source.kind === "drive_file" &&
      effectiveOutputStrategy === "in_place" &&
      recovery.isSuccess &&
      unresolved === null &&
      completedResult === null,
    queryFn: async ({ signal }) =>
      source.kind === "drive_file"
        ? await DOCUMENT_TRANSFORM_API.current(source.fileId, requestedMode, signal)
        : null,
    queryKey: [
      "document-transform-current",
      owner,
      source.kind === "drive_file" ? source.fileId : "markdown",
      requestedMode,
    ],
    retry: false,
    staleTime: 0,
  });
  const currentResult = currentPolish.data ?? null;
  const currentCheckRequired = effectiveOutputStrategy === "in_place";
  const currentCheckPending = currentCheckRequired && currentPolish.isFetching;
  const sessionIdentityUnavailable = !owner;
  const optionsLocked = submitting || unresolved !== null || completedResult !== null;
  const saveRecovery = (operation: null | TransformRecovery) => {
    writeTransformRecovery(owner, sourceIdentity, operation);
    queryClient.setQueryData(recoveryKey, operation);
    if (owner && sourceIdentity === "markdown" && (!operation || operation.taskId))
      clearMarkdownRecovery(owner);
  };
  const ensureShortLink = async (
    result: DocumentTransformResult,
  ): Promise<DocumentTransformResult> => {
    const ensured = await ensureDocumentAliasAction(result.outputFile.id);
    if (!ensured.ok) throw new Error(ensured.error);
    queryClient.setQueryData(documentAliasKey(result.outputFile.id), {
      alias: ensured.data.alias,
    });
    return {
      ...result,
      shortLinkAlias: ensured.data.alias,
      shortLinkStatus: "ready",
    };
  };

  useEffect(() => {
    const refresh = () => {
      void queryClient.invalidateQueries({
        queryKey: ["document-transform-recovery", owner, sourceIdentity],
      });
      if (source.kind === "drive_file") {
        void queryClient.invalidateQueries({
          queryKey: ["document-transform-current", owner, source.fileId],
        });
      }
    };
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [owner, source, sourceIdentity, queryClient]);

  useEffect(
    () => () => {
      lifecycleGeneration.current += 1;
      fileLoadGeneration.current += 1;
      fileLoadPending.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
      submitLatch.current = false;
      idempotencyKey.current = null;
      refreshedDriveSource.current = null;
    },
    [sourceIdentity, owner],
  );

  const resetRequest = () => {
    if (unresolved) return;
    setRetryMessage(null);
    idempotencyKey.current = null;
    refreshedDriveSource.current = null;
    fileLoadGeneration.current += 1;
    fileLoadPending.current = false;
    setLoadingMarkdownFile(false);
    setReviewMessage(null);
    setReviewResult(null);
    setTerminalMessage(null);
    setTerminalTaskId(null);
    setUnverifiedOutputId(null);
    setUnverifiedOutputIsOriginal(false);
  };

  const close = () => {
    setRetryMessage(null);
    abortRef.current?.abort();
    abortRef.current = null;
    submitLatch.current = false;
    if (unresolved) {
      setSubmitting(false);
      setStage("idle");
      onOpenChange(false);
      return;
    }
    idempotencyKey.current = null;
    refreshedDriveSource.current = null;
    fileLoadGeneration.current += 1;
    fileLoadPending.current = false;
    setLoadingMarkdownFile(false);
    setMode("both");
    setOutputStrategy("copy");
    setMarkdown("");
    setUploadedMarkdownId(null);
    setMarkdownError(null);
    setName("Untitled document");
    setCopyName("");
    setCompletedResult(null);
    setDestination(source.kind === "markdown" ? source.initialDestination : null);
    setDestinationOpen(false);
    setSubmitting(false);
    setStage("idle");
    setReviewMessage(null);
    setReviewResult(null);
    setTerminalMessage(null);
    setTerminalTaskId(null);
    setUnverifiedOutputId(null);
    setUnverifiedOutputIsOriginal(false);
    onOpenChange(false);
  };

  const recoveredMarkdownSource =
    source.kind === "markdown" &&
    restoredDraft?.owner === owner &&
    restoredDraft.key === unresolved?.key
      ? restoredDraft.source
      : null;
  const inputSource = (): DocumentTransformSource | null => {
    if (recoveredMarkdownSource) return recoveredMarkdownSource;
    if (source.kind === "drive_file") {
      return {
        expectedModifiedTime: source.expectedModifiedTime,
        fileId: source.fileId,
        kind: source.kind,
        name: source.name,
      };
    }
    if (!destination || !name.trim() || !markdown.trim() || markdownError !== null) return null;
    if (uploadedMarkdownId) {
      return {
        destinationFolderId: destination.folderId,
        kind: "uploaded_markdown",
        name: name.trim(),
        uploadId: uploadedMarkdownId,
      };
    }
    return {
      content: markdown,
      destinationFolderId: destination.folderId,
      kind: "markdown",
      name: name.trim(),
    };
  };

  const loadMarkdownFile = async (file: File) => {
    const generation = fileLoadGeneration.current + 1;
    fileLoadGeneration.current = generation;
    fileLoadPending.current = true;
    setLoadingMarkdownFile(true);
    try {
      const imported = await readMarkdownFile(file);
      if (fileLoadGeneration.current !== generation) return;
      setUploadedMarkdownId(null);
      const uploadId = await DOCUMENT_TRANSFORM_API.uploadMarkdown(file);
      if (fileLoadGeneration.current !== generation) return;
      setUploadedMarkdownId(uploadId);
      setMarkdown(imported.content);
      setName(imported.name);
      setMarkdownError(markdownTextError(imported.content));
      idempotencyKey.current = null;
      setReviewMessage(null);
      setReviewResult(null);
      setTerminalMessage(null);
      setTerminalTaskId(null);
      setRetryMessage(null);
    } catch (error: unknown) {
      if (fileLoadGeneration.current === generation) {
        setMarkdownError(errorMessage(error, "Couldn’t read the Markdown file"));
      }
    } finally {
      if (fileLoadGeneration.current === generation) {
        fileLoadPending.current = false;
        setLoadingMarkdownFile(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    }
  };

  const create = async () => {
    if (fileLoadPending.current) return;
    if (
      currentCheckRequired &&
      (currentCheckPending || currentPolish.isError || currentResult !== null)
    )
      return;
    if (sessionIdentityUnavailable && !unresolved) return;
    const startedAt = Date.now();
    let transformSource = unresolved?.source ?? inputSource();
    if (!transformSource && unresolved?.taskId) {
      // A known task needs no source content; this placeholder is never submitted.
      transformSource = { kind: "markdown", content: "", name: "", destinationFolderId: "" };
    }
    if ((!transformSource && !unresolved?.taskId) || submitLatch.current) return;
    if (!transformSource) return;
    submitLatch.current = true;
    setRetryMessage(null);
    setSubmitting(true);
    setStage("starting");
    setReviewMessage(null);
    setReviewResult(null);
    const controller = new AbortController();
    const generation = lifecycleGeneration.current;
    const isCurrent = () =>
      lifecycleGeneration.current === generation && !controller.signal.aborted;
    const finishSubmission = () => {
      if (abortRef.current !== controller) return;
      abortRef.current = null;
      submitLatch.current = false;
      setSubmitting(false);
      setStage("idle");
    };
    abortRef.current?.abort();
    abortRef.current = controller;
    if (unresolved) {
      // Replay the exact admitted payload, never refreshed metadata under the same key.
    } else if (transformSource.kind === "drive_file" && refreshedDriveSource.current) {
      transformSource = refreshedDriveSource.current;
    } else if (transformSource.kind === "drive_file" && source.kind === "drive_file") {
      try {
        const expectedModifiedTime = await source.refreshExpectedModifiedTime();
        controller.signal.throwIfAborted();
        transformSource = { ...transformSource, expectedModifiedTime };
        cacheRefreshedDriveSource(refreshedDriveSource, transformSource);
      } catch (error: unknown) {
        if (!(error instanceof DOMException && error.name === "AbortError") && isCurrent()) {
          const message = errorMessage(error, "Couldn’t verify the latest document revision");
          setRetryMessage(message);
          toast.error(message);
        }
        finishSubmission();
        return;
      }
    }
    if (!isCurrent()) {
      finishSubmission();
      return;
    }
    const requestIdempotencyKey =
      unresolved?.key ?? (idempotencyKey.current ??= crypto.randomUUID());
    const candidateOperation: TransformRecovery = unresolved ?? {
      createdAt: startedAt,
      key: requestIdempotencyKey,
      mode: requestedMode,
      outputStrategy: effectiveOutputStrategy,
      outputName: effectiveOutputStrategy === "copy" ? copyName.trim() || null : null,
      source: transformSource.kind === "drive_file" ? transformSource : null,
      taskId: null,
    };
    let operation = candidateOperation;
    let knownTaskId = operation.taskId;
    let replayedSubmission: "active" | "terminal" | null = null;
    const reserve = async (candidate: TransformRecovery) => {
      const reserved = await claimTransformRecovery(owner, sourceIdentity, candidate);
      controller.signal.throwIfAborted();
      saveRecovery(reserved);
      assertRecoveryReplaySafe(
        reserved,
        unresolved !== null,
        transformSource.kind === "uploaded_markdown"
          ? transformSource.uploadId
          : transformSource.kind === "markdown"
            ? transformSource.content
            : markdown,
      );
      return reserved;
    };
    const clearRecovery = async () => {
      await completeTransformRecovery(owner, sourceIdentity, operation.key);
      saveRecovery(null);
    };
    const submitTask = async () => {
      try {
        return await DOCUMENT_TRANSFORM_API.create(
          operation.source ?? transformSource,
          operation.mode,
          operation.key,
          operation.outputStrategy,
          ...(operation.outputName ? [operation.outputName] : []),
        );
      } catch (error: unknown) {
        // Only a fresh, definitively rejected submission can release its claim.
        // An adopted/replayed operation may already have been admitted by an
        // earlier request, even if today's validation rejects its payload.
        if (
          error instanceof ApiError &&
          ((error.status === 409 && error.code === "document_transform_source_changed") ||
            error.status === 422) &&
          unresolved === null &&
          operation === candidateOperation &&
          operation.taskId === null
        ) {
          await clearRecovery();
          resetRequest();
        }
        throw error;
      }
    };
    const perform = async () => {
      operation = await reserve(candidateOperation);
      knownTaskId = operation.taskId;
      const recoveredTask = operation.taskId !== null;
      const task = operation.taskId
        ? await DOCUMENT_TRANSFORM_API.getTask(operation.taskId, controller.signal)
        : await submitTask();
      if (recoveredTask || task.replayed === true) {
        replayedSubmission = task.status === "succeeded" ? "terminal" : "active";
      }
      knownTaskId = task.id;
      saveRecovery({ ...operation, taskId: task.id });
      await recordTransformTask(owner, sourceIdentity, { ...operation, taskId: task.id });
      controller.signal.throwIfAborted();
      if (!isCurrent()) return;
      setStage("transforming");
      // POST replay can return a terminal task without its permission-checked
      // incomplete-copy descriptor. Persist the task ID before this extra read
      // so a failed read remains safely resumable without another submission.
      const currentTask =
        !operation.taskId && task.status === "failed"
          ? await DOCUMENT_TRANSFORM_API.getTask(task.id, controller.signal)
          : task;
      const result = await DOCUMENT_TRANSFORM_API.waitForCompletion(currentTask, controller.signal);
      if (!isCurrent()) return;
      await clearRecovery();
      if (result.shortLinkStatus === "ready" && result.shortLinkAlias) {
        queryClient.setQueryData(["document-alias", result.outputFile.id], {
          alias: result.shortLinkAlias,
        });
      }
      if (result.outputStrategy === "in_place") {
        await queryClient.invalidateQueries({ queryKey: wikiKeys.file(result.outputFile.id) });
        if (!isCurrent()) return;
        onInPlaceComplete?.();
        toast.success(
          replayedSubmission === "terminal"
            ? "Already polished — showing the existing result"
            : replayedSubmission === "active"
              ? "Resumed the existing polish"
              : result.changed
                ? `Updated “${result.outputFile.name}”`
                : "No changes needed",
        );
      } else {
        injectNewFileIntoCaches(queryClient, result.outputFile);
        toast.success(
          replayedSubmission === "terminal"
            ? "Already polished — showing the existing result"
            : replayedSubmission === "active"
              ? "Resumed the existing polish"
              : `Created “${result.outputFile.name}”`,
        );
      }
      setCompletedResult(result);
    };
    try {
      await perform();
    } catch (error: unknown) {
      if (error instanceof DocumentTransformNeedsReviewError) {
        if (!isCurrent()) return;
        await completeTransformRecovery(owner, sourceIdentity, operation.key);
        saveRecovery(null);
        setReviewMessage(error.message);
        setTerminalTaskId(knownTaskId);
        setReviewResult(error.result ?? null);
        if (error.result?.outputStrategy === "in_place") {
          await queryClient.invalidateQueries({
            queryKey: wikiKeys.file(error.result.outputFile.id),
          });
          if (!isCurrent()) return;
          onInPlaceComplete?.();
        } else if (error.result) {
          injectNewFileIntoCaches(queryClient, error.result.outputFile);
        }
        toast.warning(
          preservedInPlaceOutcome(error.result)?.heading ?? `The ${artifactNoun} needs review`,
        );
      } else if (!(error instanceof DOMException && error.name === "AbortError")) {
        if (!isCurrent()) return;
        if (error instanceof DocumentTransformTerminalError) {
          await completeTransformRecovery(owner, sourceIdentity, operation.key);
          saveRecovery(null);
          setTerminalMessage(error.message);
          setTerminalTaskId(knownTaskId);
          setUnverifiedOutputId(error.unverifiedOutputId ?? null);
          setUnverifiedOutputIsOriginal(error.code === "in_place_verification_failed");
        } else {
          setRetryMessage(errorMessage(error, `Couldn’t polish ${artifactNoun}`));
        }
        toast.error(errorMessage(error, `Couldn’t polish ${artifactNoun}`));
      }
    } finally {
      finishSubmission();
    }
  };

  const valid = inputSource() !== null;
  const displayedMarkdown =
    recoveredMarkdownSource?.kind === "markdown" ? recoveredMarkdownSource.content : markdown;
  const displayedMode = completedResult?.mode ?? unresolved?.mode ?? requestedMode;
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
        <DialogContent
          aria-busy={submitting}
          className="max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-xl"
          onEscapeKeyDown={(event) => {
            if (submitting) event.preventDefault();
          }}
          showCloseButton={!submitting}
        >
          <div className="border-b px-5 pt-5 pb-4 sm:px-6 sm:pt-6">
            <DialogHeader>
              <DialogTitle>
                {source.kind === "markdown" ? "Import Markdown" : `Polish ${artifactNoun}`}
              </DialogTitle>
              <DialogDescription>
                {source.kind === "markdown"
                  ? "Create a native Google Doc from pasted Markdown or a local .md file."
                  : isWord
                    ? "Create a Word review copy with supported font updates. Wording and document structure stay unchanged; review pagination and fields in Word."
                    : `Choose how Sarj Wiki should refine this ${artifactNoun}.`}
              </DialogDescription>
            </DialogHeader>
          </div>

          <div
            className="space-y-5 overflow-y-auto p-5 sm:px-6"
            data-polish-scroll-body
            ref={scrollBodyRef}
          >
            {terminalMessage ? (
              <div
                className="border-destructive/30 bg-destructive/5 rounded-lg border px-3 py-2.5 text-sm"
                role="alert"
              >
                <h3 className="font-medium" ref={outcomeHeadingRef} tabIndex={-1}>
                  Polish did not complete
                </h3>
                <p className="text-muted-foreground mt-1 text-xs">{terminalMessage}</p>
                {terminalTaskId ? (
                  <p className="text-muted-foreground mt-2 text-xs">
                    Task reference: <code className="break-all select-all">{terminalTaskId}</code>
                  </p>
                ) : null}
                {unverifiedOutputId ? (
                  <p className="mt-2 text-xs">
                    {unverifiedOutputIsOriginal
                      ? "The original document may have changed and has not passed safety verification. Inspect it and its Drive version history before trying again. "
                      : "This copy is incomplete and has not passed safety verification. Review it before using its content. "}
                    <a
                      className="underline"
                      href={`/d/${encodeURIComponent(unverifiedOutputId)}`}
                      rel="noopener noreferrer"
                      target="_blank"
                    >
                      {unverifiedOutputIsOriginal
                        ? "Open original document for review"
                        : "Open incomplete copy for review"}
                    </a>
                  </p>
                ) : null}
                <p className="text-muted-foreground mt-1 text-xs">
                  Change an option to start a deliberate new attempt.
                </p>
              </div>
            ) : null}
            {reviewMessage ? (
              <div
                className="border-border bg-muted/50 rounded-lg border px-3 py-2.5 text-sm"
                role="alert"
              >
                <h3 className="font-medium" ref={outcomeHeadingRef} tabIndex={-1}>
                  {preservedInPlaceOutcome(reviewResult)?.heading ??
                    `This ${artifactNoun} needs review`}
                </h3>
                <p className="text-muted-foreground mt-1 text-xs">
                  {preservedInPlaceOutcome(reviewResult)?.message ?? reviewMessage}
                </p>
                {terminalTaskId ? (
                  <p className="text-muted-foreground mt-2 text-xs">
                    Task reference: <code className="break-all select-all">{terminalTaskId}</code>
                  </p>
                ) : null}
                {reviewResult ? (
                  <DocumentPolishReceipt
                    {...(reviewResult.shortLinkStatus === "unavailable"
                      ? {
                          onRetryShortLink: async () => {
                            const updated = await ensureShortLink(reviewResult);
                            setReviewResult((current) =>
                              current?.operationId === updated.operationId ? updated : current,
                            );
                          },
                        }
                      : {})}
                    result={reviewResult}
                  />
                ) : null}
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
                    {preservedInPlaceOutcome(reviewResult)?.openLabel ??
                      (reviewResult.outputStrategy === "in_place"
                        ? "Open original document"
                        : "Open review copy")}
                  </Button>
                ) : null}
              </div>
            ) : null}
            {completedResult ? (
              <div className="space-y-3" role="status">
                <h3 className="text-lg font-semibold" ref={outcomeHeadingRef} tabIndex={-1}>
                  {completedResult.changed ? "Polish complete" : "No changes needed"}
                </h3>
                <p className="font-medium break-words">{completedResult.outputFile.name}</p>
                <p className="text-muted-foreground text-sm">
                  {completedResult.mode === "format"
                    ? "Formatting-only mode leaves wording unchanged."
                    : `${completedResult.appliedPatchCount} writing edit${completedResult.appliedPatchCount === 1 ? "" : "s"} applied.`}
                  {completedResult.validation.protectedContentPreserved
                    ? " Protected content checks passed."
                    : " Review protected content before using this result."}
                  {completedResult.outputStrategy === "in_place"
                    ? completedResult.changed
                      ? " Updated the original; Drive version history is available."
                      : " Your original is unchanged; no edits were needed."
                    : " Your original is unchanged."}
                </p>
                <p className="text-muted-foreground text-xs">
                  Task reference:{" "}
                  <code className="break-all select-all">{completedResult.operationId}</code>
                </p>
                <DocumentPolishReceipt
                  {...(completedResult.shortLinkStatus === "unavailable"
                    ? {
                        onRetryShortLink: async () => {
                          const updated = await ensureShortLink(completedResult);
                          setCompletedResult((current) =>
                            current?.operationId === updated.operationId ? updated : current,
                          );
                        },
                      }
                    : {})}
                  result={completedResult}
                />
              </div>
            ) : null}
            {!completedResult && !unresolved && currentCheckRequired && currentResult ? (
              <div className="space-y-3" role="status">
                <h3 className="text-lg font-semibold">Already polished</h3>
                <p className="font-medium break-words">{currentResult.outputFile.name}</p>
                <p className="text-muted-foreground text-sm">
                  This exact document revision already completed {currentResult.mode} polish with
                  the current pipeline. Edit the document or choose another method to start new
                  work.
                </p>
                <p className="text-muted-foreground text-xs">
                  Task reference:{" "}
                  <code className="break-all select-all">{currentResult.operationId}</code>
                </p>
                <DocumentPolishReceipt result={currentResult} />
              </div>
            ) : null}
            {sessionIdentityUnavailable ? (
              <div className="border-border rounded-lg border px-3 py-2.5 text-sm" role="alert">
                <p className="font-medium">Secure session identity unavailable</p>
                <p className="text-muted-foreground mt-1 text-xs">
                  Reload your session before starting polish. Existing operations are preserved;
                  this does not sign you out.
                </p>
                <Button
                  className="mt-2"
                  onClick={() => router.refresh()}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  Reload session
                </Button>
              </div>
            ) : null}
            {unresolved && !submitting ? (
              <p className="text-sm" role="status">
                A polish operation is already reserved for this document. Resume checking its
                status; closing this dialog does not cancel it.
                {unresolved.taskId
                  ? ` Task: ${unresolved.taskId}`
                  : " Its submission is being recovered."}
              </p>
            ) : null}
            {recovery.isError ? (
              <p role="alert">
                Couldn’t safely restore polish status. Restore browser storage access before
                starting another operation.
              </p>
            ) : null}
            {source.kind === "markdown" && !completedResult ? (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="polish-markdown-name">Document name</Label>
                  <Input
                    disabled={optionsLocked}
                    id="polish-markdown-name"
                    maxLength={255}
                    onChange={(event) => {
                      fileLoadGeneration.current += 1;
                      setName(event.target.value);
                      resetRequest();
                    }}
                    value={recoveredMarkdownSource?.name ?? name}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Markdown file</Label>
                  <input
                    accept=".md, text/markdown, text/plain"
                    aria-hidden="true"
                    className="sr-only"
                    disabled={optionsLocked}
                    onChange={(event) => {
                      const file = event.target.files?.item(0);
                      if (file) void loadMarkdownFile(file);
                    }}
                    ref={fileInputRef}
                    tabIndex={-1}
                    type="file"
                  />
                  <Button
                    aria-label="Markdown file drop zone"
                    className="bg-muted/25 h-auto w-full flex-wrap justify-start border-dashed p-3 whitespace-normal"
                    disabled={optionsLocked}
                    onClick={() => fileInputRef.current?.click()}
                    onDragOver={(event) => {
                      event.preventDefault();
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      if (optionsLocked) return;
                      const file = event.dataTransfer.files.item(0);
                      if (file) void loadMarkdownFile(file);
                    }}
                    type="button"
                    variant="outline"
                  >
                    <FileUp className="size-4" />
                    <span className="text-sm font-medium">Choose .md file</span>
                    <span className="text-muted-foreground text-xs">
                      or drop one here · UTF-8 · 512 KiB max
                    </span>
                  </Button>
                  {loadingMarkdownFile ? (
                    <p aria-live="polite" className="text-muted-foreground text-xs" role="status">
                      Reading Markdown file…
                    </p>
                  ) : null}
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-baseline justify-between gap-3">
                    <Label htmlFor="polish-markdown-content">Markdown</Label>
                    <span className="text-muted-foreground text-xs tabular-nums">
                      {markdownByteLength(displayedMarkdown).toLocaleString()} /{" "}
                      {MARKDOWN_LIMIT_BYTES.toLocaleString()} bytes
                    </span>
                  </div>
                  <Textarea
                    className="min-h-44 resize-y"
                    disabled={optionsLocked}
                    id="polish-markdown-content"
                    onChange={(event) => {
                      fileLoadGeneration.current += 1;
                      const value = event.target.value;
                      setUploadedMarkdownId(null);
                      setMarkdown(value);
                      setMarkdownError(markdownTextError(value));
                      resetRequest();
                    }}
                    placeholder={"# Proposal\n\nPaste Markdown here…"}
                    value={displayedMarkdown}
                    variant="code"
                  />
                  {markdownError ? (
                    <p className="text-destructive text-xs" role="alert">
                      {markdownError}
                    </p>
                  ) : null}
                </div>
                <div className="space-y-1.5">
                  <Label>Destination</Label>
                  <Button
                    className="h-auto w-full justify-between px-3 py-2 text-left"
                    disabled={optionsLocked}
                    onClick={() => {
                      setDestinationOpen(true);
                    }}
                    type="button"
                    variant="outline"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {recoveredMarkdownSource
                          ? "Saved destination folder"
                          : (destination?.driveName ?? "No workspace selected")}
                      </span>
                      <span className="text-muted-foreground block truncate text-xs">
                        {recoveredMarkdownSource
                          ? `Folder ID: ${recoveredMarkdownSource.destinationFolderId}`
                          : destinationLabel}
                      </span>
                    </span>
                    <span className="text-muted-foreground shrink-0 text-xs">Change</span>
                  </Button>
                </div>
              </div>
            ) : null}

            {source.kind === "drive_file" && effectiveOutputStrategy === "copy" ? (
              <div className="space-y-1.5">
                <Label htmlFor="polish-copy-name">Copy name (optional)</Label>
                <Input
                  disabled={optionsLocked}
                  id="polish-copy-name"
                  maxLength={255}
                  onChange={(event) => {
                    setCopyName(event.target.value);
                    resetRequest();
                  }}
                  placeholder="Use the automatic name"
                  value={copyName}
                />
                <p className="text-muted-foreground text-xs">
                  Leave blank for an automatic name. Editing the original keeps its current name.
                </p>
              </div>
            ) : null}

            <fieldset className="space-y-2">
              <legend className="mb-2 text-sm font-medium">Transform method</legend>
              {isWord &&
              unresolved &&
              (unresolved.mode !== "format" || unresolved.outputStrategy !== "copy") ? (
                <p className="text-muted-foreground text-xs" role="status">
                  An earlier operation is reserved with its original settings. Resume checks that
                  exact operation; it will not be changed to Word formatting.
                </p>
              ) : null}
              <div className="grid gap-2 sm:grid-cols-3">
                {MODES.filter(
                  (option) => !isWord || unresolved !== null || option.value === "format",
                ).map((option) => {
                  const Icon = option.icon;
                  const selected = displayedMode === option.value;
                  return (
                    <Button
                      aria-pressed={selected}
                      className={cn(
                        "focus-visible:ring-ring/50 relative block h-auto rounded-lg border p-3 text-left font-normal whitespace-normal transition-[border-color,background-color,box-shadow] outline-none focus-visible:ring-[3px] disabled:opacity-50",
                        selected
                          ? "border-primary bg-primary/5 hover:bg-primary/5 dark:hover:bg-primary/5 shadow-sm"
                          : "border-border hover:bg-muted/50",
                      )}
                      disabled={optionsLocked}
                      key={option.value}
                      onClick={() => {
                        setMode(option.value);
                        resetRequest();
                      }}
                      type="button"
                      variant="ghost"
                    >
                      <span className="flex items-center gap-2">
                        <Icon className={cn("size-4", selected && "text-primary")} />
                        <span className="text-sm font-semibold">{option.label}</span>
                      </span>
                      <span className="text-muted-foreground mt-2 block text-xs leading-relaxed">
                        {isWord && option.value === "format"
                          ? "Update supported Latin fonts only. Preserve sizes, emphasis, tables, headers, footers, and fields."
                          : option.description}
                      </span>
                      {option.value === "both" ? (
                        <span className="text-primary mt-2 block text-[10px] font-semibold tracking-wide uppercase">
                          Recommended
                        </span>
                      ) : null}
                    </Button>
                  );
                })}
              </div>
            </fieldset>

            {source.kind === "drive_file" &&
            source.allowInPlace &&
            !isWord &&
            capabilities.data?.inPlace === true ? (
              <fieldset className="space-y-2">
                <legend className="mb-2 text-sm font-medium">Save result</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Button
                    aria-describedby={`${saveDescriptionId}-copy`}
                    aria-label="Create review copy"
                    aria-pressed={outputStrategy === "copy"}
                    className={cn(
                      "focus-visible:ring-ring/50 block h-auto rounded-lg border p-3 text-left font-normal whitespace-normal outline-none focus-visible:ring-[3px] disabled:opacity-50",
                      outputStrategy === "copy"
                        ? "border-primary bg-primary/5 hover:bg-primary/5 dark:hover:bg-primary/5 shadow-sm"
                        : "border-border hover:bg-muted/50",
                    )}
                    disabled={optionsLocked}
                    onClick={() => {
                      setOutputStrategy("copy");
                      resetRequest();
                    }}
                    type="button"
                    variant="ghost"
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold">
                      <Copy aria-hidden="true" className="size-4" />
                      Create review copy
                    </span>
                    <span
                      className="text-muted-foreground mt-2 block text-xs leading-relaxed"
                      id={`${saveDescriptionId}-copy`}
                    >
                      Keep the original untouched and create a sibling {outputNoun}.
                    </span>
                    <span className="text-primary mt-2 block text-[10px] font-semibold tracking-wide uppercase">
                      Recommended
                    </span>
                  </Button>
                  <Button
                    aria-describedby={`${saveDescriptionId}-original`}
                    aria-label="Edit original"
                    aria-pressed={outputStrategy === "in_place"}
                    className={cn(
                      "focus-visible:ring-ring/50 block h-auto rounded-lg border p-3 text-left font-normal whitespace-normal outline-none focus-visible:ring-[3px] disabled:opacity-50",
                      outputStrategy === "in_place"
                        ? "border-primary bg-primary/5 hover:bg-primary/5 dark:hover:bg-primary/5 shadow-sm"
                        : "border-border hover:bg-muted/50",
                    )}
                    disabled={optionsLocked}
                    onClick={() => {
                      setOutputStrategy("in_place");
                      resetRequest();
                    }}
                    type="button"
                    variant="ghost"
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold">
                      <FilePenLine aria-hidden="true" className="size-4" />
                      Edit original
                    </span>
                    <span
                      className="text-muted-foreground mt-2 block text-xs leading-relaxed"
                      id={`${saveDescriptionId}-original`}
                    >
                      Amend this Google Doc; Drive version history remains available.
                    </span>
                  </Button>
                </div>
              </fieldset>
            ) : null}

            {capabilityPending ? (
              <div aria-live="polite" className="text-muted-foreground text-xs">
                Checking whether this document can be safely edited in place…
              </div>
            ) : null}
            {capabilityFailed ? (
              <div className="space-y-2 text-xs" role="alert">
                <p className="text-muted-foreground">
                  Edit options could not be checked. No polish will start until you retry.
                </p>
                <Button
                  onClick={() => {
                    void capabilities.refetch();
                  }}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  Retry edit options
                </Button>
                {!optionsLocked && !recovery.isPending && !recovery.isError && owner ? (
                  <Button
                    onClick={() => {
                      try {
                        if (readTransformRecovery(owner, sourceIdentity)) {
                          void recovery.refetch();
                          return;
                        }
                        window.location.reload();
                      } catch {
                        void recovery.refetch();
                      }
                    }}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    Reload page
                  </Button>
                ) : null}
                <p className="text-muted-foreground">
                  If retry keeps failing after an update, reload this page to load the latest
                  version. Reloading does not start polish or clear saved operations.
                </p>
              </div>
            ) : null}
            {currentCheckPending ? (
              <div aria-live="polite" className="text-muted-foreground text-xs">
                Checking whether this exact revision is already polished…
              </div>
            ) : null}
            {currentCheckRequired && !unresolved && currentPolish.isError ? (
              <div className="space-y-2 text-xs" role="alert">
                <p className="text-muted-foreground">
                  The latest polish state could not be verified. Retry before editing the original,
                  or create a review copy.
                </p>
                <Button
                  onClick={() => {
                    void currentPolish.refetch();
                  }}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  Retry polish check
                </Button>
              </div>
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
                    : `“${source.name}” stays unchanged. A new ${outputNoun} will be created next to it.`
                  : "Your local file and pasted Markdown stay unchanged. A new Google Doc will be created in the selected folder."}
              </p>
            </div>

            {submitting ? (
              <div
                aria-live="polite"
                className="border-primary/20 bg-primary/5 flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm"
                role="status"
              >
                <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
                <span>
                  {stage === "starting"
                    ? source.kind === "markdown"
                      ? "Starting Markdown import…"
                      : `Starting ${artifactNoun} polish…`
                    : source.kind === "markdown"
                      ? "Importing and checking the new document…"
                      : effectiveOutputStrategy === "in_place"
                        ? `Updating and checking the original ${artifactNoun}…`
                        : `Creating and checking the new ${artifactNoun}…`}
                  {unresolved?.taskId
                    ? ` Checking its status automatically. Leaving this page does not cancel it. Task: ${unresolved.taskId}`
                    : " Checking the submission automatically. Leaving this page does not cancel a submitted operation."}
                </span>
              </div>
            ) : null}

            {source.kind === "markdown" && owner && unresolved && !submitting ? (
              <DocumentPolishRecovery
                key={`${owner}:${unresolved.key}`}
                onRestore={(operation, recoveredSource) => {
                  saveRecovery(operation);
                  if (recoveredSource)
                    setRestoredDraft({ owner, key: operation.key, source: recoveredSource });
                }}
                operation={unresolved}
                owner={owner}
                source={inputSource()}
              />
            ) : null}
            {retryMessage ? (
              <div className="border-destructive/30 rounded-lg border p-3 text-sm" role="alert">
                <p className="font-medium">Polish interrupted</p>
                <p className="text-muted-foreground mt-1 text-xs">{retryMessage}</p>
                <p className="text-muted-foreground mt-1 text-xs">
                  {unresolved
                    ? "Your existing operation is still reserved. Resume checking to recover its status; options remain locked to prevent duplicate work."
                    : "You can retry, change the options, or close this dialog."}
                </p>
              </div>
            ) : null}
          </div>

          <DialogFooter className="bg-background border-t px-5 py-4 sm:flex sm:px-6">
            <Button
              className="w-full sm:w-auto"
              disabled={submitting}
              onClick={close}
              type="button"
              variant="ghost"
            >
              {completedResult ? "Done" : "Cancel"}
            </Button>
            <Button
              className="w-full sm:w-auto"
              disabled={
                submitting ||
                loadingMarkdownFile ||
                (!completedResult &&
                  (recovery.isPending ||
                    recovery.isError ||
                    (!unresolved && sessionIdentityUnavailable) ||
                    (!unresolved && (capabilityPending || capabilityFailed)) ||
                    (!unresolved &&
                      currentCheckRequired &&
                      (currentCheckPending || currentPolish.isError || currentResult !== null)) ||
                    reviewMessage !== null ||
                    terminalMessage !== null ||
                    (!valid && !unresolved?.taskId)))
              }
              onClick={() => {
                if (completedResult) {
                  const fileId = completedResult.outputFile.id;
                  close();
                  router.push(`/d/${encodeURIComponent(fileId)}`);
                } else {
                  void create();
                }
              }}
              type="button"
            >
              {submitting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              {completedResult
                ? "Open polished document"
                : submitting
                  ? source.kind === "markdown"
                    ? "Importing…"
                    : "Polishing…"
                  : unresolved
                    ? "Resume checking"
                    : capabilityPending
                      ? "Checking edit options…"
                      : currentCheckPending
                        ? "Checking polish status…"
                        : currentCheckRequired && currentResult
                          ? "Already polished"
                          : effectiveOutputStrategy === "in_place"
                            ? "Polish original"
                            : source.kind === "markdown"
                              ? "Import Google Doc"
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

function assertRecoveryReplaySafe(
  operation: TransformRecovery,
  recovering: boolean,
  markdown: string,
): void {
  // Backend replay retention is seven days; leave a one-day safety margin.
  if (!operation.taskId && Date.now() - operation.createdAt >= 6 * 24 * 60 * 60 * 1000) {
    throw new Error(
      "This submission is too old to replay safely. Ask an administrator to check its task status before starting another polish.",
    );
  }
  if (recovering && !operation.source && !operation.taskId && !markdown.trim()) {
    throw new Error(
      "The previous import may still be running. Keep this operation reserved and check task status before starting another import.",
    );
  }
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

function cacheRefreshedDriveSource(
  target: { current: Extract<DocumentTransformSource, { kind: "drive_file" }> | null },
  source: Extract<DocumentTransformSource, { kind: "drive_file" }>,
): void {
  target.current = source;
}
