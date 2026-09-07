import { renderToStaticMarkup } from "react-dom/server";

import type { DocumentTransformResult } from "@/lib/document-transform-types";
import { MIME_DOC } from "@/types/wiki";

import { DocumentPolishReceipt } from "./document-polish-receipt";

it.each([0, 1, 2, 20])(
  "reports %s native heading promotions without claiming rewritten text",
  (count) => {
    const result: DocumentTransformResult = {
      accessScope: "owner_only",
      appliedPatchCount: 0,
      artifactKind: "document",
      brandProfileId: "test",
      brandProfileSha256: "test",
      changed: true,
      deterministicPatchCount: 0,
      findings: [],
      llmInvoked: false,
      mode: "format",
      modelIds: [],
      modelPatchCount: 0,
      needsReview: false,
      operationId: "55555555-5555-4555-8555-555555555555",
      outputFile: {
        id: "output",
        name: "Review",
        mimeType: MIME_DOC,
        canEdit: true,
        driveId: undefined,
        modifiedTime: undefined,
        parentFolderId: undefined,
      },
      outputRevisionId: null,
      outputStrategy: "copy",
      outputWebUrl: null,
      pipelineVersion: "test",
      promotedHeadingCount: count,
      sourceRevisionId: null,
      sourceSha256: "test",
      toolVersions: {},
      validation: {
        protectedContentPreserved: true,
        sourceUnchanged: true,
        structurePreserved: true,
        unsafePatchCount: 0,
      },
    };
    const view = renderToStaticMarkup(<DocumentPolishReceipt result={result} />);
    expect(view.includes("native heading")).toBe(count > 0);
    expect(
      view.includes(
        `Formatted ${count} existing section ${count === 1 ? "label as a native heading" : "labels as native headings"}; label wording is unchanged.`,
      ),
    ).toBe(count > 0);
    expect(view).not.toContain("writing edits applied");
  },
);

it("separates verified preservation coverage from review notes", () => {
  const result: DocumentTransformResult = {
    accessScope: "owner_only",
    appliedPatchCount: 0,
    artifactKind: "document",
    brandProfileId: "test",
    brandProfileSha256: "test",
    changed: true,
    deterministicPatchCount: 0,
    findings: [
      {
        blockId: "rich",
        code: "native_rich_text_preserved",
        detail: "Rich structure was preserved safely.",
        severity: "informational",
      },
      {
        blockId: null,
        code: "brand_mismatch",
        detail: "Review the header.",
        severity: "review",
      },
    ],
    llmInvoked: false,
    mode: "format",
    modelIds: [],
    modelPatchCount: 0,
    needsReview: true,
    operationId: "55555555-5555-4555-8555-555555555555",
    outputFile: {
      id: "output",
      name: "Review",
      mimeType: MIME_DOC,
      canEdit: true,
      driveId: undefined,
      modifiedTime: undefined,
      parentFolderId: undefined,
    },
    outputRevisionId: null,
    outputStrategy: "copy",
    outputWebUrl: null,
    pipelineVersion: "test",
    promotedHeadingCount: 0,
    sourceRevisionId: null,
    sourceSha256: "test",
    toolVersions: {},
    validation: {
      protectedContentPreserved: true,
      sourceUnchanged: true,
      structurePreserved: true,
      unsafePatchCount: 1,
    },
  };

  const view = renderToStaticMarkup(<DocumentPolishReceipt result={result} />);
  expect(view).toContain("Preserved safely");
  expect(view).toContain("Rich structure was preserved safely.");
  expect(view).toContain("Review notes");
  expect(view).toContain("Review the header.");
});
