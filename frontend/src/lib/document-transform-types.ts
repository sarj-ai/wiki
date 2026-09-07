import type { FileResponse } from "@/types/wiki";

export type DocumentTransformMode = "both" | "format" | "unslop";
export type DocumentTransformOutputStrategy = "copy" | "in_place";

export type DocumentTransformSource =
  | {
      expectedModifiedTime: string;
      fileId: string;
      kind: "drive_file";
      name: string;
    }
  | {
      content: string;
      destinationFolderId: string;
      kind: "markdown";
      name: string;
    };

export interface DocumentTransformResult {
  accessScope: "inherited" | "owner_only";
  appliedPatchCount: number;
  artifactKind: "document" | "presentation";
  brandProfileId: string;
  brandProfileSha256: string;
  deterministicPatchCount: number;
  findings: DocumentTransformFinding[];
  mode: DocumentTransformMode;
  llmInvoked: boolean;
  modelPatchCount: number;
  promotedHeadingCount: number;
  modelIds: string[];
  needsReview: boolean;
  operationId: string;
  outputStrategy: DocumentTransformOutputStrategy;
  outputFile: FileResponse;
  outputWebUrl: null | string;
  pipelineVersion: string;
  changed: boolean;
  outputRevisionId: null | string;
  sourceRevisionId: null | string;
  sourceSha256: string;
  shortLinkAlias?: null | string;
  shortLinkStatus?: "ready" | "unavailable" | null;
  toolVersions: Record<string, string>;
  validation: DocumentTransformValidation;
}

export interface DocumentTransformFinding {
  blockId: null | string;
  code: string;
  detail: string;
  severity?: "informational" | "review";
}

export interface DocumentTransformValidation {
  protectedContentPreserved: boolean;
  sourceUnchanged: boolean;
  structurePreserved: boolean;
  unsafePatchCount: number;
}

export type DocumentTransformTask =
  | {
      errorCode: string;
      errorDetail?: string;
      id: string;
      status: "failed";
      unverifiedOutputId?: string;
    }
  | {
      id: string;
      result: DocumentTransformResult;
      status: "succeeded";
    }
  | {
      id: string;
      status: "cancelled" | "pending" | "running";
    };

export class DocumentTransformNeedsReviewError extends Error {
  readonly result: DocumentTransformResult | undefined;

  constructor(message: string, result?: DocumentTransformResult) {
    super(message);
    this.name = "DocumentTransformNeedsReviewError";
    this.result = result;
  }
}

export class DocumentTransformTerminalError extends Error {
  readonly code: string;
  readonly unverifiedOutputId: string | undefined;

  constructor(code: string, message: string, unverifiedOutputId?: string) {
    super(message);
    this.code = code;
    this.unverifiedOutputId = unverifiedOutputId;
    this.name = "DocumentTransformTerminalError";
  }
}
