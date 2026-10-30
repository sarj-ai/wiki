import type { FileResponse } from "@/types/wiki";
import type { MIME_DOC } from "@/types/wiki";
import { MIME_SLIDE } from "@/types/wiki";

export type SmartFileType = "document" | "presentation";

export interface DriveTemplate {
  categoryPath: string[];
  description?: string;
  driveId: string;
  driveName: string;
  fileId: string;
  mimeType: typeof MIME_DOC | typeof MIME_SLIDE;
  modifiedTime: string;
  name: string;
}

export type AuthoringMode = AIMode | TemplateMode;

export type CreationSource =
  | {
      fileType: SmartFileType;
      kind: "blank";
    }
  | {
      kind: "template";
      mimeType: typeof MIME_DOC | typeof MIME_SLIDE;
      templateDriveId: string;
      templateFileId: string;
      templateModifiedTime: string;
    };

export interface SmartDestination {
  driveId: string;
  driveName: string;
  folderId: string;
  folderPath: string[];
}

export interface SmartCreationPlan {
  alias: string;
  destination: SmartDestination;
  emoji: string;
  name: string;
  source: CreationSource;
}
export type SmartCreateState =
  | { mode: AuthoringMode; phase: "compose" }
  | { mode: ReadyAuthoringMode; phase: "review"; plan: SmartCreationPlan };

export interface SmartCreateResult {
  file: FileResponse;
  taskId: string;
  warnings: { code: string; detail: string }[];
}

export type SmartCreateTask =
  | {
      errorCode: string;
      errorDetail?: string;
      id: string;
      status: "failed";
    }
  | {
      id: string;
      result: SmartCreateResult;
      status: "succeeded";
    }
  | {
      id: string;
      status: "cancelled";
    }
  | {
      id: string;
      status: "pending" | "running";
    };

export function initialSmartCreateState(): SmartCreateState {
  return {
    mode: { description: "", fileType: "document", kind: "ai" },
    phase: "compose",
  };
}

export function switchAuthoringMode(
  state: SmartCreateState,
  kind: AuthoringMode["kind"],
): SmartCreateState {
  if (state.mode.kind === kind && state.phase === "compose") return state;
  return kind === "ai"
    ? { mode: { description: "", fileType: "document", kind: "ai" }, phase: "compose" }
    : { mode: { kind: "template", selection: { kind: "unselected" } }, phase: "compose" };
}

export function sourceForTemplate(template: DriveTemplate): CreationSource {
  return {
    kind: "template",
    mimeType: template.mimeType,
    templateDriveId: template.driveId,
    templateFileId: template.fileId,
    templateModifiedTime: template.modifiedTime,
  };
}

export function defaultAlias(name: string): string {
  const words = name
    .toLowerCase()
    .replaceAll(/[^a-z\s-]/g, " ")
    .split(/[\s-]+/)
    .filter((word) => word.length >= 2)
    .slice(0, 3)
    .map((word) => word.slice(0, 12));
  return words.length >= 2 ? words.join("-") : "new-document";
}

export function defaultEmoji(mimeType: string): string {
  return mimeType === MIME_SLIDE ? "📊" : "📄";
}

interface AIMode {
  description: string;
  fileType: SmartFileType;
  kind: "ai";
}

type TemplateSelection = { kind: "selected"; template: DriveTemplate } | { kind: "unselected" };

interface TemplateMode {
  kind: "template";
  selection: TemplateSelection;
}

type ReadyAuthoringMode =
  | AIMode
  | (TemplateMode & {
      selection: Extract<TemplateSelection, { kind: "selected" }>;
    });
