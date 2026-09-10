import { describe, expect, it, vi } from "vitest";

import { WIKI_READ_API } from "@/lib/wiki-read-api";

const actionMocks = vi.hoisted(() => ({
  getFile: vi.fn(),
  searchDrive: vi.fn(),
}));

vi.mock("@/app/actions/wiki-actions", () => ({
  getCurrentUserAction: vi.fn(),
  getDocumentAliasAction: vi.fn(),
  getDriveFoldersAction: vi.fn(),
  getDrivesAction: vi.fn(),
  getDriveSearchSnippetAction: vi.fn(),
  getFileAction: actionMocks.getFile,
  getFolderFilesAction: vi.fn(),
  getPreferencesAction: vi.fn(),
  listDocIconsAction: vi.fn(),
  listFolderIconsAction: vi.fn(),
  listSortOrdersAction: vi.fn(),
  searchDriveAction: actionMocks.searchDrive,
}));

describe("WIKI_READ_API", () => {
  it("routes typed file reads through the Server Action", async () => {
    const file = {
      canEdit: true,
      driveId: "drive-1",
      id: "file-1",
      mimeType: "application/vnd.google-apps.document",
      modifiedTime: undefined,
      name: "Plan",
      parentFolderId: "folder-1",
    };
    actionMocks.getFile.mockResolvedValue({ data: file, ok: true });

    await expect(WIKI_READ_API.getFile("file-1")).resolves.toBe(file);
    expect(actionMocks.getFile).toHaveBeenCalledWith("file-1");
  });

  it("preserves search parameters at the action boundary", async () => {
    actionMocks.searchDrive.mockResolvedValue({ data: [], ok: true });

    await expect(WIKI_READ_API.driveSearch("launch", 10)).resolves.toEqual([]);
    expect(actionMocks.searchDrive).toHaveBeenCalledWith("launch", 10);
  });
});
