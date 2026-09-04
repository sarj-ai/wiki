import * as z from "zod";

import {
  createDocumentTransformAction,
  getDocumentTransformCapabilitiesAction,
  getSmartCreateTaskAction,
} from "@/app/actions/smart-create-actions";
import { runAction } from "@/lib/action-client";
import {
  DocumentTransformNeedsReviewError,
  type DocumentTransformOutputStrategy,
  type DocumentTransformResult,
  type DocumentTransformSource,
  type DocumentTransformTask,
} from "@/lib/document-transform-types";

const ZMode = z.enum(["format", "unslop", "both"]);
const ZOutputStrategy = z.enum(["copy", "in_place"]);
const ZCapabilities = z.object({
  copy: z.literal(true),
  in_place: z.boolean(),
  presentation_copy: z.literal(true).default(true),
  presentation_in_place: z.literal(false).default(false),
});
const ZMessage = z
  .object({
    block_id: z.string().nullable(),
    code: z.string(),
    detail: z.string(),
  })
  .passthrough();
const ZResult = z.object({
  access_scope: z.enum(["inherited", "owner_only"]).default("inherited"),
  applied_patch_count: z.int().nonnegative(),
  artifact_kind: z.enum(["document", "presentation"]).default("document"),
  brand_profile_id: z.string().default("sarj-legacy-v1"),
  brand_profile_sha256: z.string().default("legacy-unrecorded"),
  deterministic_patch_count: z.int().nonnegative().default(0),
  findings: z.array(ZMessage),
  mode: ZMode,
  llm_invoked: z.boolean().default(true),
  model_patch_count: z.int().nonnegative().default(0),
  model_ids: z.array(z.string()),
  needs_review: z.boolean(),
  operation_id: z.uuid(),
  output_strategy: ZOutputStrategy.default("copy"),
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
  source_revision_id: z.string().nullable().default(null),
  tool_versions: z.record(z.string(), z.string()),
  validation: z.object({
    protected_content_preserved: z.boolean(),
    source_unchanged: z.boolean(),
    structure_preserved: z.boolean(),
    unsafe_patch_count: z.int().nonnegative(),
  }),
});
const ZTask = z.discriminatedUnion("status", [
  z.object({ id: z.uuid(), status: z.enum(["pending", "running", "cancelled"]) }),
  z.object({
    error_code: z.string(),
    error_detail: z.string().nullable(),
    id: z.uuid(),
    status: z.literal("failed"),
  }),
  z.object({ id: z.uuid(), result: ZResult, status: z.literal("succeeded") }),
]);

const DEFAULT_TASK_POLL_MS = 1_000;
const DEFAULT_MAX_TASK_POLLS = 900;

export const documentTransformApi = {
  capabilities: async () => {
    const responseBody = await runAction(
      async () => await getDocumentTransformCapabilitiesAction(),
    );
    const capabilities = ZCapabilities.parse(JSON.parse(responseBody));
    return {
      copy: capabilities.copy,
      inPlace: capabilities.in_place,
      presentationCopy: capabilities.presentation_copy,
      presentationInPlace: capabilities.presentation_in_place,
    } as const;
  },

  create: async (
    source: DocumentTransformSource,
    mode: "both" | "format" | "unslop",
    idempotencyKey: string,
    outputStrategy: DocumentTransformOutputStrategy = "copy",
  ): Promise<DocumentTransformTask> => {
    const body = JSON.stringify({
      mode,
      output_name: null,
      output_strategy: outputStrategy,
      source: serializeSource(source),
    });
    const responseBody = await runAction(
      async () => await createDocumentTransformAction(body, idempotencyKey),
    );
    return parseTask(ZTask.parse(JSON.parse(responseBody)));
  },

  getTask: async (taskId: string, signal?: AbortSignal): Promise<DocumentTransformTask> => {
    signal?.throwIfAborted();
    const responseBody = await runAction(async () => await getSmartCreateTaskAction(taskId));
    signal?.throwIfAborted();
    return parseTask(ZTask.parse(JSON.parse(responseBody)));
  },

  waitForCompletion: async (
    initialTask: DocumentTransformTask,
    signal?: AbortSignal,
    pollIntervalMs = DEFAULT_TASK_POLL_MS,
    maxPolls = DEFAULT_MAX_TASK_POLLS,
  ): Promise<DocumentTransformResult> =>
    await waitForTask(initialTask, signal, pollIntervalMs, maxPolls),
};

async function waitForTask(
  task: DocumentTransformTask,
  signal: AbortSignal | undefined,
  pollIntervalMs: number,
  maxPolls: number,
): Promise<DocumentTransformResult> {
  let current = task;
  for (let poll = 0; ; poll += 1) {
    switch (current.status) {
      case "cancelled":
        throw new Error("Document polish was cancelled");
      case "failed":
        if (current.errorCode === "needs_review") {
          throw new DocumentTransformNeedsReviewError(
            current.errorDetail ?? "The result needs review before it can be opened.",
          );
        }
        throw new Error(current.errorDetail ?? `Document polish failed (${current.errorCode})`);
      case "succeeded":
        if (current.result.needsReview) {
          throw new DocumentTransformNeedsReviewError(
            "Automated safety checks marked this copy for review.",
            current.result,
          );
        }
        return current.result;
      case "pending":
      case "running":
        if (poll >= maxPolls) {
          throw new Error("Document polish timed out; check its task status before retrying");
        }
        await abortableDelay(pollIntervalMs, signal);
        current = await documentTransformApi.getTask(current.id, signal);
    }
  }
}

function parseTask(task: z.infer<typeof ZTask>): DocumentTransformTask {
  switch (task.status) {
    case "cancelled":
    case "pending":
    case "running":
      return { id: task.id, status: task.status };
    case "failed":
      return {
        errorCode: task.error_code,
        ...(task.error_detail === null ? {} : { errorDetail: task.error_detail }),
        id: task.id,
        status: "failed",
      };
    case "succeeded":
      return { id: task.id, result: parseResult(task.result), status: "succeeded" };
  }
}

function parseResult(result: z.infer<typeof ZResult>): DocumentTransformResult {
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
    })),
    mode: result.mode,
    llmInvoked: result.llm_invoked,
    modelPatchCount: result.model_patch_count,
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
  return source.kind === "drive_file"
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

async function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(
          signal.reason instanceof Error
            ? signal.reason
            : new DOMException("The operation was aborted", "AbortError"),
        );
      },
      { once: true },
    );
  });
}
