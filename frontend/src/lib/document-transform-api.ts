import * as z from "zod";

import { abortableDelay } from "@/lib/abortable-delay";
import { runAction, runActionResult } from "@/lib/action-client";
import { ApiError } from "@/lib/api-error";
import { captureBrowserSession } from "@/lib/browser-session";
import { transformHttp } from "@/lib/clients/transform-http";
import { discardStream } from "@/lib/discard-stream";
import {
  DocumentTransformNeedsReviewError,
  type DocumentTransformOutputStrategy,
  type DocumentTransformResult,
  type DocumentTransformSource,
  type DocumentTransformTask,
  DocumentTransformTerminalError,
} from "@/lib/document-transform-types";
import { preservedInPlaceOutcome } from "@/lib/preserved-in-place-outcome";
import { TransformModeSchema } from "@/lib/transform-mode-schema";

const OutputStrategySchema = z.enum(["copy", "in_place"]);
const FindingSeveritySchema = z.enum(["informational", "review"]);
const AccessScopeSchema = z.enum(["inherited", "owner_only"]);
const ArtifactKindSchema = z.enum(["document", "presentation"]);
const ShortLinkStatusSchema = z.enum(["ready", "unavailable"]);
const TaskStatusSchema = z.enum(["pending", "running", "succeeded", "failed", "cancelled"]);
const UuidSchema = z.uuid();
const AdmissionSchema = z.strictObject({ task_id: UuidSchema, status: TaskStatusSchema });
const CapabilitiesSchema = z.object({
  copy: z.literal(true),
  in_place: z.boolean(),
  presentation_copy: z.literal(true).default(true),
  presentation_in_place: z.literal(false).default(false),
});
const MessageSchema = z
  .object({
    block_id: z.string().nullable(),
    code: z.string(),
    detail: z.string(),
    severity: FindingSeveritySchema.default("review"),
  })
  .loose();
const ResultSchema = z
  .object({
    access_scope: AccessScopeSchema.default("inherited"),
    applied_patch_count: z.int().nonnegative(),
    artifact_kind: ArtifactKindSchema.default("document"),
    brand_profile_id: z.string().default("sarj-legacy-v1"),
    brand_profile_sha256: z.string().default("legacy-unrecorded"),
    deterministic_patch_count: z.int().nonnegative().default(0),
    findings: z.array(MessageSchema),
    mode: TransformModeSchema,
    llm_invoked: z.boolean().default(true),
    model_patch_count: z.int().nonnegative().default(0),
    promoted_heading_count: z.int().min(0).max(20).default(0),
    model_ids: z.array(z.string()),
    needs_review: z.boolean(),
    operation_id: UuidSchema,
    output_strategy: OutputStrategySchema.default("copy"),
    output: z.object({
      file_id: z.string(),
      name: z.string(),
      parent_folder_id: z.string(),
      web_url: z.string().nullable(),
      media_type: z.string().default("application/vnd.google-apps.document"),
    }),
    pipeline_version: z.string(),
    changed: z.boolean().default(true),
    output_revision_id: z.string().nullable().default(null),
    source_sha256: z.string(),
    short_link_alias: z.string().nullable().default(null),
    short_link_status: ShortLinkStatusSchema.nullable().default(null),
    source_revision_id: z.string().nullable().default(null),
    tool_versions: z.record(z.string(), z.string()),
    validation: z.object({
      protected_content_preserved: z.boolean(),
      source_unchanged: z.boolean(),
      structure_preserved: z.boolean(),
      unsafe_patch_count: z.int().nonnegative(),
    }),
  })
  .transform((result) => ({
    ...result,
    needs_review:
      result.needs_review || result.findings.some((finding) => finding.severity === "review"),
  }));
const TaskSchema = z.discriminatedUnion("status", [
  z.object({ id: UuidSchema, status: TaskStatusSchema.exclude(["failed", "succeeded"]) }),
  z.object({
    error_code: z.string(),
    error_detail: z.string().nullable(),
    id: UuidSchema,
    status: z.literal("failed"),
    unverified_output: z.object({ file_id: z.string().min(1) }).nullish(),
  }),
  z.object({ id: UuidSchema, result: ResultSchema, status: z.literal("succeeded") }),
]);
const CurrentPolishSchema = z.object({ result: ResultSchema.nullable() });
const UploadSchema = z.strictObject({
  upload_id: UuidSchema,
  url: z.url().startsWith("https://storage.googleapis.com/"),
  method: z.literal("PUT"),
  headers: z.record(z.string(), z.string()),
  expires_in_seconds: z.int().positive(),
});

const DEFAULT_TASK_POLL_MS = 1_000;
const DEFAULT_MAX_TASK_POLLS = 900;
const MAX_TRANSIENT_POLL_FAILURES = 5;
const REDACTED_TASK_FAILURE_DETAIL = "Task could not be completed";
const TASK_FAILURE_FALLBACKS: Readonly<Record<string, string>> = {
  google_auth_required: "Reconnect Google Drive, then start the document polish again.",
  header_conflict: "Review the document header before applying Sarj branding again.",
  incompatible_pending_output:
    "An incomplete result from an earlier polish needs review before you try again.",
  in_place_not_allowed:
    "This document is no longer eligible for safe in-place polishing. Reload it or create a review copy.",
  in_place_verification_failed:
    "Google Docs accepted changes, but safety verification failed. The original document may have changed; inspect it and its Drive version history before trying again.",
  needs_review:
    "Sarj stopped at a safety check. Review the document and its Drive version history before trying again.",
  output_verification_failed:
    "The polished result did not pass safety verification. Review the document before trying again.",
  source_changed:
    "The document changed after polishing started. Reload and review the latest version before trying again.",
};

export const DOCUMENT_TRANSFORM_API = {
  uploadMarkdown: async (file: File): Promise<string> => {
    const body = await runAction(async () => await transformHttp("/uploads", "{}"));
    const upload = UploadSchema.parse(JSON.parse(body));
    const response = await fetch(upload.url, {
      method: upload.method,
      headers: upload.headers,
      body: file,
      signal: AbortSignal.timeout(60_000),
    });
    discardStream(response.body);
    if (!response.ok) throw new Error("Markdown upload failed. Choose the file again to retry.");
    return upload.upload_id;
  },
  admission: async (key: string, signal?: AbortSignal) => {
    const id = UuidSchema.parse(key);
    try {
      const raw = await runAction(
        async () =>
          await transformHttp(
            `/admissions/${encodeURIComponent(id)}`,
            undefined,
            undefined,
            signal,
          ),
        signal,
      );
      return AdmissionSchema.parse(JSON.parse(raw));
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },
  capabilities: async (signal?: AbortSignal) => {
    const responseBody = await runAction(
      async () => await transformHttp("/capabilities", undefined, undefined, signal),
      signal,
    );
    const capabilities = CapabilitiesSchema.parse(JSON.parse(responseBody));
    return {
      copy: capabilities.copy,
      inPlace: capabilities.in_place,
      presentationCopy: capabilities.presentation_copy,
      presentationInPlace: capabilities.presentation_in_place,
    } as const;
  },

  current: async (fileId: string, mode: "both" | "format" | "unslop", signal?: AbortSignal) => {
    const responseBody = await runAction(
      async () =>
        await transformHttp(
          `/current?file_id=${encodeURIComponent(fileId)}&mode=${encodeURIComponent(mode)}`,
          undefined,
          undefined,
          signal,
        ),
      signal,
    );
    const current = CurrentPolishSchema.parse(JSON.parse(responseBody));
    return current.result === null ? null : parseResult(current.result);
  },

  create: async (
    source: DocumentTransformSource,
    mode: "both" | "format" | "unslop",
    idempotencyKey: string,
    outputStrategy: DocumentTransformOutputStrategy = "copy",
    outputName: null | string = null,
  ): Promise<DocumentTransformTask> => {
    const body = JSON.stringify({
      mode,
      output_name: outputName,
      output_strategy: outputStrategy,
      source: serializeSource(source),
    });
    const response = await runActionResult(
      async () => await transformHttp("", body, idempotencyKey),
    );
    return {
      ...parseTask(TaskSchema.parse(JSON.parse(response.data))),
      ...(!(response.replayed === undefined) && { replayed: response.replayed }),
    };
  },

  getTask: async (taskId: string, signal?: AbortSignal): Promise<DocumentTransformTask> => {
    signal?.throwIfAborted();
    const responseBody = await runAction(
      async () =>
        await transformHttp(`/tasks/${encodeURIComponent(taskId)}`, undefined, undefined, signal),
      signal,
    );
    signal?.throwIfAborted();
    return parseTask(TaskSchema.parse(JSON.parse(responseBody)));
  },

  waitForCompletion: async (
    initialTask: DocumentTransformTask,
    signal?: AbortSignal,
    pollIntervalMs = DEFAULT_TASK_POLL_MS,
    maxPolls = DEFAULT_MAX_TASK_POLLS,
  ): Promise<DocumentTransformResult> =>
    await waitForTask(initialTask, signal, pollIntervalMs, maxPolls),
} as const;

async function waitForTask(
  task: DocumentTransformTask,
  signal: AbortSignal | undefined,
  pollIntervalMs: number,
  maxPolls: number,
): Promise<DocumentTransformResult> {
  const owner = captureBrowserSession();
  const pollSignal = signal === undefined ? owner : AbortSignal.any([owner, signal]);
  let current = task;
  let transientFailures = 0;
  for (let poll = 0; ; poll += 1) {
    pollSignal.throwIfAborted();
    switch (current.status) {
      case "cancelled":
        throw new DocumentTransformTerminalError("Document polish was cancelled", {
          code: "cancelled",
        });
      case "failed":
        if (current.errorCode === "needs_review" && !current.unverifiedOutputId) {
          throw new DocumentTransformNeedsReviewError(
            taskFailureDetail(
              current.errorCode,
              current.errorDetail,
              "The result needs review before it can be opened.",
            ),
          );
        }
        throw new DocumentTransformTerminalError(
          taskFailureDetail(
            current.errorCode,
            current.errorDetail,
            `Document polish failed (${current.errorCode})`,
          ),
          { code: current.errorCode, unverifiedOutputId: current.unverifiedOutputId },
        );
      case "succeeded":
        if (current.result.needsReview) {
          throw new DocumentTransformNeedsReviewError(
            preservedInPlaceOutcome(current.result)?.message ??
              (current.result.outputStrategy === "in_place"
                ? "Automated safety checks marked this document for review."
                : "Automated safety checks marked this copy for review."),
            { result: current.result },
          );
        }
        return current.result;
      case "pending":
      case "running":
        if (poll >= maxPolls) {
          throw new Error("Document polish timed out; check its task status before retrying");
        }
        // eslint-disable-next-line no-await-in-loop -- Polling must wait before issuing the next status request.
        await abortableDelay(pollIntervalMs, pollSignal);
        try {
          // eslint-disable-next-line no-await-in-loop -- Each poll depends on the preceding task status.
          current = await DOCUMENT_TRANSFORM_API.getTask(current.id, pollSignal);
          transientFailures = 0;
        } catch (error: unknown) {
          if (!isTransientPollError(error) || transientFailures >= MAX_TRANSIENT_POLL_FAILURES) {
            throw error;
          }
          transientFailures += 1;
          // eslint-disable-next-line no-await-in-loop -- Backoff must finish before the next attempt.
          await abortableDelay(pollIntervalMs * 2 ** transientFailures, pollSignal);
        }
    }
  }
}

function taskFailureDetail(
  errorCode: string,
  errorDetail: string | undefined,
  fallback: string,
): string {
  if (errorDetail && errorDetail !== REDACTED_TASK_FAILURE_DETAIL) return errorDetail;
  return TASK_FAILURE_FALLBACKS[errorCode] ?? fallback;
}

function parseTask(task: z.infer<typeof TaskSchema>): DocumentTransformTask {
  switch (task.status) {
    case "cancelled":
    case "pending":
    case "running":
      return { id: task.id, status: task.status };
    case "failed":
      return {
        errorCode: task.error_code,
        ...(!(task.error_detail === null) && { errorDetail: task.error_detail }),
        id: task.id,
        status: "failed",
        ...(!!task.unverified_output && { unverifiedOutputId: task.unverified_output.file_id }),
      };
    case "succeeded":
      return { id: task.id, result: parseResult(task.result), status: "succeeded" };
  }
}

function parseResult(result: z.infer<typeof ResultSchema>): DocumentTransformResult {
  return {
    accessScope: result.access_scope,
    appliedPatchCount: result.applied_patch_count,
    artifactKind: result.artifact_kind,
    brandProfileId: result.brand_profile_id,
    brandProfileSha256: result.brand_profile_sha256,
    deterministicPatchCount: result.deterministic_patch_count,
    findings: result.findings.map((finding) => ({
      blockId: finding.block_id,
      code: finding.code,
      detail: finding.detail,
      severity: finding.severity,
    })),
    mode: result.mode,
    llmInvoked: result.llm_invoked,
    modelPatchCount: result.model_patch_count,
    promotedHeadingCount: result.promoted_heading_count,
    modelIds: result.model_ids,
    needsReview: result.needs_review,
    operationId: result.operation_id,
    outputStrategy: result.output_strategy,
    outputFile: {
      canEdit: true,
      driveId: undefined,
      id: result.output.file_id,
      mimeType: result.output.media_type,
      modifiedTime: undefined,
      name: result.output.name,
      parentFolderId: result.output.parent_folder_id,
    },
    outputWebUrl: result.output.web_url,
    pipelineVersion: result.pipeline_version,
    changed: result.changed,
    outputRevisionId: result.output_revision_id,
    sourceSha256: result.source_sha256,
    shortLinkAlias: result.short_link_alias,
    shortLinkStatus: result.short_link_status,
    sourceRevisionId: result.source_revision_id,
    toolVersions: result.tool_versions,
    validation: {
      protectedContentPreserved: result.validation.protected_content_preserved,
      sourceUnchanged: result.validation.source_unchanged,
      structurePreserved: result.validation.structure_preserved,
      unsafePatchCount: result.validation.unsafe_patch_count,
    },
  };
}

function serializeSource(source: DocumentTransformSource): Record<string, string> {
  return source.kind === "uploaded_markdown"
    ? {
        destination_folder_id: source.destinationFolderId,
        kind: source.kind,
        name: source.name,
        upload_id: source.uploadId,
      }
    : source.kind === "drive_file"
      ? {
          expected_modified_time: source.expectedModifiedTime,
          file_id: source.fileId,
          kind: source.kind,
        }
      : {
          content: source.content,
          destination_folder_id: source.destinationFolderId,
          kind: source.kind,
          name: source.name,
        };
}

function isTransientPollError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 408 || error.status === 429 || error.status >= 500)
  );
}
