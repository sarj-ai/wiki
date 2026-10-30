import type { FileResponse } from "@/types/wiki";

export type DocumentTransformMode = "both" | "format" | "unslop";
export type DocumentTransformOutputStrategy = "copy" | "in_place";

export type DocumentTransformSource =
  | {
      content: string;
      destinationFolderId: string;
      kind: "markdown";
      name: string;
    }
  | {
      destinationFolderId: string;
      kind: "uploaded_markdown";
      name: string;
      uploadId: string;
    }
  | {
      expectedModifiedTime: string;
      fileId: string;
      kind: "drive_file";
      name: string;
    };

export interface DocumentTransformResult {
  accessScope: "inherited" | "owner_only";
  appliedPatchCount: number;
  artifactKind: "document" | "presentation";
  brandProfileId: string;
  brandProfileSha256: string;
  changed: boolean;
  deterministicPatchCount: number;
  findings: DocumentTransformFinding[];
  llmInvoked: boolean;
  mode: DocumentTransformMode;
  modelIds: string[];
  modelPatchCount: number;
  needsReview: boolean;
  operationId: string;
  outputFile: FileResponse;
  outputRevisionId: null | string;
  outputStrategy: DocumentTransformOutputStrategy;
  outputWebUrl: null | string;
  pipelineVersion: string;
  promotedHeadingCount: number;
  shortLinkAlias?: null | string;
  shortLinkStatus?: "ready" | "unavailable" | null;
  sourceRevisionId: null | string;
  sourceSha256: string;
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

export type DocumentTransformTask = (
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
    }
) & { replayed?: boolean };

export class DocumentTransformNeedsReviewError extends Error {
  readonly result: DocumentTransformResult | undefined;

  constructor(message: string, options?: ErrorOptions & { result?: DocumentTransformResult }) {
    super(message, options);
    this.name = "DocumentTransformNeedsReviewError";
    this.result = options?.result;
  }
}

export class DocumentTransformTerminalError extends Error {
  readonly code: string;
  readonly unverifiedOutputId: string | undefined;

  constructor(
    message: string,
    options: ErrorOptions & { code: string; unverifiedOutputId?: string | undefined },
  ) {
    super(message, options);
    this.code = options.code;
    this.unverifiedOutputId = options.unverifiedOutputId;
    this.name = "DocumentTransformTerminalError";
  }
}
