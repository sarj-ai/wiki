import * as z from "zod";

import {
  createDocumentTransformAction,
  getSmartCreateTaskAction,
} from "@/app/actions/smart-create-actions";
import { runAction } from "@/lib/action-client";
import {
  DocumentTransformNeedsReviewError,
  type DocumentTransformResult,
  type DocumentTransformSource,
  type DocumentTransformTask,
} from "@/lib/document-transform-types";

const ZMode = z.enum(["format", "unslop", "both"]);
const ZMessage = z
  .object({
    block_id: z.string().nullable(),
    code: z.string(),
    detail: z.string(),
  })
  .passthrough();
const ZResult = z.object({
  applied_patch_count: z.int().nonnegative(),
  findings: z.array(ZMessage),
  mode: ZMode,
  model_ids: z.array(z.string()),
  needs_review: z.boolean(),
  operation_id: z.uuid(),
  output: z.object({
    file_id: z.string(),
    name: z.string(),
    parent_folder_id: z.string(),
    web_url: z.string().nullable(),
  }),
  pipeline_version: z.string(),
  source_sha256: z.string(),
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
  create: async (
    source: DocumentTransformSource,
    mode: "both" | "format" | "unslop",
    idempotencyKey: string,
  ): Promise<DocumentTransformTask> => {
    const body = JSON.stringify({ mode, output_name: null, source: serializeSource(source) });
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
          throw new Error("Document polish timed out; the source remains unchanged");
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
    appliedPatchCount: result.applied_patch_count,
    findings: result.findings.map((finding) => ({
      blockId: finding.block_id,
      code: finding.code,
      detail: finding.detail,
    })),
    mode: result.mode,
    modelIds: result.model_ids,
    needsReview: result.needs_review,
    operationId: result.operation_id,
    outputFile: {
      canEdit: true,
      driveId: undefined,
      id: result.output.file_id,
      mimeType: "application/vnd.google-apps.document",
      modifiedTime: undefined,
      name: result.output.name,
      parentFolderId: result.output.parent_folder_id,
    },
    outputWebUrl: result.output.web_url,
    pipelineVersion: result.pipeline_version,
    sourceSha256: result.source_sha256,
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
