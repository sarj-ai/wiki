import { describe, expect, it } from "vitest";

import { folderUrlFor } from "./file-urls";

describe("folderUrlFor", () => {
  it("builds an encoded Google Drive folder URL", () => {
    expect(folderUrlFor("folder/id")).toBe("https://drive.google.com/drive/folders/folder%2Fid");
  });
});
