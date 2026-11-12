import { describe, expect, it } from "vitest";

import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FORM,
  MIME_HTML,
  MIME_PDF,
  MIME_SHEET,
  MIME_SLIDE,
} from "@/types/wiki";

import { embedUrlFor, folderUrlFor, supportsDocumentMode } from "./file-urls";

describe("document modes", () => {
  it.each([
    [MIME_DOC, "document"],
    [MIME_SHEET, "spreadsheets"],
    [MIME_SLIDE, "presentation"],
  ])("switches %s between its native preview and editor", (mimeType, path) => {
    expect(supportsDocumentMode(mimeType)).toBe(true);
    expect(embedUrlFor(mimeType, "file/id", "preview")).toBe(
      `https://docs.google.com/${path}/d/file%2Fid/preview`,
    );
    expect(embedUrlFor(mimeType, "file/id", "edit")).toBe(
      `https://docs.google.com/${path}/d/file%2Fid/edit`,
    );
    expect(embedUrlFor(mimeType, "file/id")).toBe(
      embedUrlFor(mimeType, "file/id", "edit"),
    );
  });

  it.each([MIME_DRAWING, MIME_FORM, MIME_HTML, MIME_PDF])(
    "keeps existing embedding for %s without a mode switch",
    (mimeType) => {
      expect(supportsDocumentMode(mimeType)).toBe(false);
      expect(embedUrlFor(mimeType, "file", "preview")).toBe(
        embedUrlFor(mimeType, "file"),
      );
    },
  );
});

describe("folderUrlFor", () => {
  it("builds an encoded Google Drive folder URL", () => {
    expect(folderUrlFor("folder/id")).toBe(
      "https://drive.google.com/drive/folders/folder%2Fid",
    );
  });
});
