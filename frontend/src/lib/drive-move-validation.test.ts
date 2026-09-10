import { describe, expect, it } from "vitest";

import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { validateCrossDriveMove } from "./validate-cross-drive-move";

describe("validateCrossDriveMove", () => {
  it("allows files to move between drives", () => {
    expect(
      validateCrossDriveMove({
        destinationDriveId: "shared-drive",
        mimeType: MIME_DOC,
        sourceDriveId: "my-drive",
      }),
    ).toBeNull();
  });

  it("allows folders to move within a drive", () => {
    expect(
      validateCrossDriveMove({
        destinationDriveId: "my-drive",
        mimeType: MIME_FOLDER,
        sourceDriveId: "my-drive",
      }),
    ).toBeNull();
  });

  it("explains why folders cannot move between drives", () => {
    expect(
      validateCrossDriveMove({
        destinationDriveId: "shared-drive",
        mimeType: MIME_FOLDER,
        sourceDriveId: "my-drive",
      }),
    ).toBe("Google Drive can’t move folders between drives. Move the folder’s files instead.");
  });
});
