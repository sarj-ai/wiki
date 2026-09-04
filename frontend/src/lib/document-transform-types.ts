import type { FileResponse } from "@/types/wiki";

export type DocumentTransformMode = "both" | "format" | "unslop";

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
  appliedPatchCount: number;
  brandProfileId: string;
  brandProfileSha256: string;
  deterministicPatchCount: number;
  findings: DocumentTransformFinding[];
  mode: DocumentTransformMode;
  llmInvoked: boolean;
  modelPatchCount: number;
  modelIds: string[];
  needsReview: boolean;
  operationId: string;
  outputFile: FileResponse;
  outputWebUrl: null | string;
  pipelineVersion: string;
  sourceSha256: string;
  toolVersions: Record<string, string>;
  validation: DocumentTransformValidation;
}

export interface DocumentTransformFinding {
  blockId: null | string;
  code: string;
  detail: string;
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
