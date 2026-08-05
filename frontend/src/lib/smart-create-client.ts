import * as z from "zod";

import {
  createSmartFileAction,
  getSmartCreateTaskAction,
  listSmartTemplatesAction,
  proposeSmartFileAction,
} from "@/app/actions/smart-create-actions";
import { runAction } from "@/lib/action-client";
import { zFileResponse } from "@/lib/generated/zod.gen";
import type {
  CreationSource,
  DriveTemplate,
  SmartCreateResult,
  SmartCreateTask,
  SmartCreationPlan,
  SmartFileType,
} from "@/lib/smart-create-types";
import { MIME_DOC, MIME_SLIDE } from "@/types/wiki";

const ZTemplate = z
  .object({
    category_path: z.array(z.string()),
    description: z.string().nullable(),
    drive_id: z.string(),
    drive_name: z.string(),
    file_id: z.string(),
    mime_type: z.enum([MIME_DOC, MIME_SLIDE]),
    modified_time: z.string(),
    name: z.string(),
  })
  .strict();

const ZSource = z.discriminatedUnion("kind", [
  z.object({ file_type: z.enum(["document", "presentation"]), kind: z.literal("blank") }).strict(),
  z
    .object({
      kind: z.literal("template"),
      mime_type: z.enum([MIME_DOC, MIME_SLIDE]),
      template_drive_id: z.string(),
      template_file_id: z.string(),
      template_modified_time: z.string(),
    })
    .strict(),
]);

const ZProposal = z
  .object({
    alias: z.string(),
    destination: z
      .object({
        drive_id: z.string(),
        drive_name: z.string(),
        folder_id: z.string(),
        folder_path: z.array(z.string()),
      })
      .strict(),
    emoji: z.string(),
    name: z.string(),
    source: ZSource,
  })
  .strict();

const ZFileResponse = zFileResponse.strict();

const ZCreateResult = z
  .object({
    file: ZFileResponse,
    task_id: z.uuid(),
    warnings: z.array(z.object({ code: z.string(), detail: z.string() }).strict()),
  })
  .strict();

const ZTemplateCatalog = z.object({ templates: z.array(ZTemplate) }).strict();

const ZTaskBase = {
  attempt_count: z.number().int().nonnegative(),
  created_at: z.iso.datetime(),
  id: z.uuid(),
  kind: z.literal("file.smart_create"),
};

const ZTask = z.discriminatedUnion("status", [
  z
    .object({
      ...ZTaskBase,
      attempt_count: z.literal(0),
      error_code: z.null(),
      error_detail: z.null(),
      finished_at: z.null(),
      resource_id: z.null(),
      result: z.null(),
      started_at: z.null(),
      status: z.literal("pending"),
    })
    .strict(),
  z
    .object({
      ...ZTaskBase,
      attempt_count: z.number().int().positive(),
      error_code: z.null(),
      error_detail: z.null(),
      finished_at: z.null(),
      resource_id: z.string().nullable(),
      result: z.null(),
      started_at: z.iso.datetime(),
      status: z.literal("running"),
    })
    .strict(),
  z
    .object({
      ...ZTaskBase,
      attempt_count: z.number().int().positive(),
      error_code: z.null(),
      error_detail: z.null(),
      finished_at: z.iso.datetime(),
      resource_id: z.string().nullable(),
      result: ZCreateResult,
      started_at: z.iso.datetime(),
      status: z.literal("succeeded"),
    })
    .strict(),
  z
    .object({
      ...ZTaskBase,
      attempt_count: z.number().int().positive(),
      error_code: z.string(),
      error_detail: z.string().nullable(),
      finished_at: z.iso.datetime(),
      resource_id: z.string().nullable(),
      result: z.null(),
      started_at: z.iso.datetime(),
      status: z.literal("failed"),
    })
    .strict(),
  z
    .object({
      ...ZTaskBase,
      attempt_count: z.literal(0),
      error_code: z.null(),
      error_detail: z.null(),
      finished_at: z.iso.datetime(),
      resource_id: z.null(),
      result: z.null(),
      started_at: z.null(),
      status: z.literal("cancelled"),
    })
    .strict(),
]);

const DEFAULT_TASK_POLL_MS = 1_000;

export interface SmartProposalInput {
  description: string;
  fileType: SmartFileType;
  invokingDriveId?: string;
  invokingFolderId?: string;
}

export const smartCreateApi = {
  create: async (plan: SmartCreationPlan, idempotencyKey: string): Promise<SmartCreateTask> => {
    const body = JSON.stringify({
      alias: plan.alias,
      emoji: plan.emoji,
      name: plan.name,
      parent_folder_id: plan.destination.folderId,
      source: serializeSource(plan.source),
    });
    const responseBody = await runAction(
      async () => await createSmartFileAction(body, idempotencyKey),
    );
    return parseTask(ZTask.parse(JSON.parse(responseBody)));
  },

  getTask: async (taskId: string, signal?: AbortSignal): Promise<SmartCreateTask> => {
    signal?.throwIfAborted();
    const responseBody = await runAction(async () => await getSmartCreateTaskAction(taskId));
    signal?.throwIfAborted();
    return parseTask(ZTask.parse(JSON.parse(responseBody)));
  },

  listTemplates: async (refresh = false): Promise<DriveTemplate[]> => {
    const responseBody = await runAction(async () => await listSmartTemplatesAction(refresh));
    const result = ZTemplateCatalog.parse(JSON.parse(responseBody));
    return result.templates.map((template) => ({
      categoryPath: template.category_path,
      ...(template.description === null ? {} : { description: template.description }),
      driveId: template.drive_id,
      driveName: template.drive_name,
      fileId: template.file_id,
      mimeType: template.mime_type,
      modifiedTime: template.modified_time,
      name: template.name,
    }));
  },

  propose: async (input: SmartProposalInput): Promise<SmartCreationPlan> => {
    const body = JSON.stringify({
      description: input.description,
      file_type: input.fileType,
      invoking_drive_id: input.invokingDriveId ?? null,
      invoking_folder_id: input.invokingFolderId ?? null,
      kind: "ai",
    });
    const responseBody = await runAction(async () => await proposeSmartFileAction(body));
    const result = ZProposal.parse(JSON.parse(responseBody));
    return {
      alias: result.alias,
      destination: {
        driveId: result.destination.drive_id,
        driveName: result.destination.drive_name,
        folderId: result.destination.folder_id,
        folderPath: result.destination.folder_path,
      },
      emoji: result.emoji,
      name: result.name,
      source: parseSource(result.source),
    };
  },

  waitForCompletion: async (
    initialTask: SmartCreateTask,
    signal?: AbortSignal,
    pollIntervalMs = DEFAULT_TASK_POLL_MS,
  ): Promise<SmartCreateResult> => await waitForTask(initialTask, signal, pollIntervalMs),
};

async function waitForTask(
  task: SmartCreateTask,
  signal: AbortSignal | undefined,
  pollIntervalMs: number,
): Promise<SmartCreateResult> {
  switch (task.status) {
    case "cancelled":
      throw new Error("File creation was cancelled");
    case "failed":
      throw new Error(task.errorDetail ?? `File creation failed (${task.errorCode})`);
    case "succeeded":
      return task.result;
    case "pending":
    case "running": {
      await abortableDelay(pollIntervalMs, signal);
      const nextTask = await smartCreateApi.getTask(task.id, signal);
      return await waitForTask(nextTask, signal, pollIntervalMs);
    }
  }
}

function parseCreateResult(result: z.infer<typeof ZCreateResult>): SmartCreateResult {
  return {
    file: {
      canEdit: result.file.can_edit,
      driveId: result.file.drive_id ?? undefined,
      id: result.file.id,
      mimeType: result.file.mime_type,
      modifiedTime: result.file.modified_time ?? undefined,
      name: result.file.name,
      parentFolderId: result.file.parent_folder_id ?? undefined,
    },
    taskId: result.task_id,
    warnings: result.warnings,
  };
}

function parseTask(task: z.infer<typeof ZTask>): SmartCreateTask {
  switch (task.status) {
    case "cancelled":
      return { id: task.id, status: "cancelled" };
    case "failed":
      return {
        errorCode: task.error_code,
        ...(task.error_detail === null ? {} : { errorDetail: task.error_detail }),
        id: task.id,
        status: "failed",
      };
    case "pending":
    case "running":
      return { id: task.id, status: task.status };
    case "succeeded":
      return { id: task.id, result: parseCreateResult(task.result), status: "succeeded" };
  }
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

function parseSource(source: z.infer<typeof ZSource>): CreationSource {
  return source.kind === "blank"
    ? { fileType: source.file_type, kind: "blank" }
    : {
        kind: "template",
        mimeType: source.mime_type,
        templateDriveId: source.template_drive_id,
        templateFileId: source.template_file_id,
        templateModifiedTime: source.template_modified_time,
      };
}

function serializeSource(source: CreationSource): z.input<typeof ZSource> {
  return source.kind === "blank"
    ? { file_type: source.fileType, kind: "blank" }
    : {
        kind: "template",
        mime_type: source.mimeType,
        template_drive_id: source.templateDriveId,
        template_file_id: source.templateFileId,
        template_modified_time: source.templateModifiedTime,
      };
}
