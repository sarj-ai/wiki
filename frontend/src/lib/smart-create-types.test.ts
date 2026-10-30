import { describe, expect, it } from "vitest";

import {
  type AuthoringMode,
  defaultAlias,
  type DriveTemplate,
  initialSmartCreateState,
  sourceForTemplate,
  switchAuthoringMode,
} from "@/lib/smart-create-types";
import { MIME_DOC } from "@/types/wiki";

const template: DriveTemplate = {
  categoryPath: ["Product", "PRDs"],
  description: "A product requirements document",
  driveId: "drive-1",
  driveName: "Product",
  fileId: "template-1",
  mimeType: MIME_DOC,
  modifiedTime: "2026-08-02T12:00:00Z",
  name: "Product Requirements",
};

describe("smart create types", () => {
  it("switching modes replaces all incompatible mode state", () => {
    const aiState = {
      mode: { description: "A launch brief", fileType: "presentation", kind: "ai" },
      phase: "compose",
    } as const;

    const templateState = switchAuthoringMode(aiState, "template");
    expect(templateState).toEqual({
      mode: { kind: "template", selection: { kind: "unselected" } },
      phase: "compose",
    });
    expect("description" in templateState.mode).toBe(false);

    const nextAiState = switchAuthoringMode(
      {
        mode: { kind: "template", selection: { kind: "selected", template } },
        phase: "compose",
      },
      "ai",
    );
    expect(nextAiState).toEqual(initialSmartCreateState());
    expect("selection" in nextAiState.mode).toBe(false);
  });

  it("encodes template creation as one complete source", () => {
    expect(sourceForTemplate(template)).toEqual({
      kind: "template",
      mimeType: MIME_DOC,
      templateDriveId: "drive-1",
      templateFileId: "template-1",
      templateModifiedTime: "2026-08-02T12:00:00Z",
    });
  });

  it("derives valid, bounded short-link candidates", () => {
    expect(defaultAlias("Product Requirements Document 2026")).toBe(
      "product-requirements-document",
    );
    expect(defaultAlias("Q3")).toBe("new-document");
  });

  it("makes contradictory authoring states fail type checking", () => {
    const invalidTemplate: AuthoringMode = {
      // @ts-expect-error Template mode cannot retain an AI description.
      description: "hidden",
      kind: "template",
      selection: { kind: "unselected" },
    };
    const invalidAi: AuthoringMode = {
      description: "brief",
      fileType: "document",
      kind: "ai",
      // @ts-expect-error AI mode cannot retain a template selection.
      selection: { kind: "selected", template },
    };
    expect([invalidTemplate, invalidAi]).toHaveLength(2);
  });
});
