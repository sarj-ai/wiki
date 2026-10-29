import * as z from "zod";

import { createSmartFileAction, proposeSmartFileAction } from "@/app/actions/smart-create-actions";
import { abortableDelay } from "@/lib/abortable-delay";
import { runAction } from "@/lib/action-client";
import { captureBrowserSession } from "@/lib/browser-session";
import { wikiReadHttp } from "@/lib/clients/wiki-read-http";
import {
  zBlankSource,
  zCancelledTaskRead,
  zFailedTaskRead,
  zFileResponse,
  zPendingTaskRead,
  zProposalDestination,
  zRunningTaskRead,
  zSmartCreateResult,
  zSmartCreateWarning,
  zSmartProposalRead,
  zSucceededTaskRead,
  zTemplateCatalogRead,
  zTemplateRead,
  zTemplateSource,
} from "@/lib/generated/zod.gen";
import type {
  CreationSource,
  DriveTemplate,
  SmartCreateResult,
  SmartCreateTask,
  SmartCreationPlan,
  SmartFileType,
} from "@/lib/smart-create-types";
const SourceSchema = z.discriminatedUnion("kind", [
  zBlankSource.strict(),
  zTemplateSource.strict(),
]);
const CreateResultSchema = zSmartCreateResult
  .extend({
    file: zFileResponse.strict(),
    warnings: z.array(zSmartCreateWarning.strict()),
  })
  .strict();
const ProposalSchema = zSmartProposalRead
  .extend({
    destination: zProposalDestination.strict(),
    source: SourceSchema,
  })
  .strict();
const TemplateCatalogSchema = zTemplateCatalogRead
  .extend({ templates: z.array(zTemplateRead.strict()) })
  .strict();
const TaskSchema = z.discriminatedUnion("status", [
  zPendingTaskRead.strict(),
  zRunningTaskRead.strict(),
  zSucceededTaskRead.extend({ result: CreateResultSchema }).strict(),
  zFailedTaskRead.strict(),
  zCancelledTaskRead.strict(),
]);

const DEFAULT_TASK_POLL_MS = 1_000;

export interface SmartProposalInput {
  description: string;
  fileType: SmartFileType;
  invokingDriveId?: string;
  invokingFolderId?: string;
}

const SmartCreateApi = {
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
    return parseTask(TaskSchema.parse(JSON.parse(responseBody)));
  },

  getTask: async (taskId: string, signal?: AbortSignal): Promise<SmartCreateTask> => {
    const task = await wikiReadHttp(`/tasks/${encodeURIComponent(taskId)}`, TaskSchema, {}, signal);
    return parseTask(task);
  },

  listTemplates: async (refresh = false, signal?: AbortSignal): Promise<DriveTemplate[]> => {
    const result = await wikiReadHttp(
      "/templates",
      TemplateCatalogSchema,
      { refresh: String(refresh) },
      signal,
    );
    return result.templates.map((template) => ({
      categoryPath: template.category_path,
      ...(!(template.description === null) && { description: template.description }),
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
    const result = ProposalSchema.parse(JSON.parse(responseBody));
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
} as const;

export { SmartCreateApi as smartCreateApi };

async function waitForTask(
  task: SmartCreateTask,
  signal: AbortSignal | undefined,
  pollIntervalMs: number,
): Promise<SmartCreateResult> {
  const owner = captureBrowserSession();
  const pollSignal = signal === undefined ? owner : AbortSignal.any([owner, signal]);
  let current = task;
  for (;;) {
    pollSignal.throwIfAborted();
    switch (current.status) {
      case "cancelled":
        throw new Error("File creation was cancelled");
      case "failed":
        throw new Error(current.errorDetail ?? `File creation failed (${current.errorCode})`);
      case "succeeded":
        return current.result;
      case "pending":
      case "running":
        // eslint-disable-next-line no-await-in-loop -- Wait before issuing the next status request.
        await abortableDelay(pollIntervalMs, pollSignal);
        // eslint-disable-next-line no-await-in-loop -- Each poll depends on the preceding task status.
        current = await SmartCreateApi.getTask(current.id, pollSignal);
    }
  }
}

function parseTask(task: z.infer<typeof TaskSchema>): SmartCreateTask {
  switch (task.status) {
    case "cancelled":
      return { id: task.id, status: "cancelled" };
    case "failed":
      return {
        errorCode: task.error_code,
        ...(!(task.error_detail === null) && { errorDetail: task.error_detail }),
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

function parseCreateResult(result: z.infer<typeof CreateResultSchema>): SmartCreateResult {
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

function parseSource(source: z.infer<typeof SourceSchema>): CreationSource {
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

function serializeSource(source: CreationSource): z.input<typeof SourceSchema> {
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
