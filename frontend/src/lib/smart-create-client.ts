import * as z from "zod";

import { makeCall } from "@/lib/api-client";
import {
  createSmartFileFilesSmartPost,
  createSmartProposalFilesSmartProposalPost,
  listTemplatesTemplatesGet,
} from "@/lib/generated/sdk.gen";
import type {
  CreationSource,
  DriveTemplate,
  SmartCreateResult,
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
    mime_type: z.union([z.literal(MIME_DOC), z.literal(MIME_SLIDE)]),
    modified_time: z.string(),
    name: z.string(),
  })
  .strict();

const ZSource = z.discriminatedUnion("kind", [
  z.object({ file_type: z.enum(["document", "presentation"]), kind: z.literal("blank") }).strict(),
  z
    .object({
      kind: z.literal("template"),
      mime_type: z.union([z.literal(MIME_DOC), z.literal(MIME_SLIDE)]),
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

const ZFileResponse = z
  .object({
    can_add_children: z.boolean().optional(),
    can_edit: z.boolean(),
    drive_id: z.string().nullable(),
    id: z.string(),
    mime_type: z.string(),
    modified_time: z.string().nullable(),
    name: z.string(),
    parent_folder_id: z.string().nullable(),
  })
  .strict();

const ZCreateResult = z
  .object({
    file: ZFileResponse,
    task_id: z.string(),
    warnings: z.array(z.object({ code: z.string(), detail: z.string() }).strict()),
  })
  .strict();

const ZTemplateCatalog = z.object({ templates: z.array(ZTemplate) }).strict();

export interface SmartProposalInput {
  description: string;
  fileType: SmartFileType;
  invokingDriveId?: string;
  invokingFolderId?: string;
}

export const smartCreateApi = {
  create: async (plan: SmartCreationPlan, idempotencyKey: string): Promise<SmartCreateResult> => {
    const result = await makeCall()(
      createSmartFileFilesSmartPost,
      {
        body: {
          alias: plan.alias,
          emoji: plan.emoji,
          name: plan.name,
          parent_folder_id: plan.destination.folderId,
          source: serializeSource(plan.source),
        },
        headers: { "Idempotency-Key": idempotencyKey },
      },
      ZCreateResult,
    );
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
  },

  listTemplates: async (refresh = false): Promise<DriveTemplate[]> => {
    const result = await makeCall()(
      listTemplatesTemplatesGet,
      { query: { refresh } },
      ZTemplateCatalog,
    );
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

  propose: async (input: SmartProposalInput, signal?: AbortSignal): Promise<SmartCreationPlan> => {
    const result = await makeCall()(
      createSmartProposalFilesSmartProposalPost,
      {
        body: {
          description: input.description,
          file_type: input.fileType,
          invoking_drive_id: input.invokingDriveId ?? null,
          invoking_folder_id: input.invokingFolderId ?? null,
          kind: "ai",
        },
        ...(signal === undefined ? {} : { signal }),
      },
      ZProposal,
    );
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
};

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
