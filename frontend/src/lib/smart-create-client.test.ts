import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { smartCreateApi } from "@/lib/smart-create-client";

const actionMocks = vi.hoisted(() => ({
  create: vi.fn(),
  propose: vi.fn(),
}));

vi.mock("@/app/actions/smart-create-actions", () => ({
  createSmartFileAction: actionMocks.create,
  proposeSmartFileAction: actionMocks.propose,
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("smartCreateApi", () => {
  it("transforms Drive templates at the transport boundary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        templates: [
          {
            category_path: ["Product", "PRDs"],
            description: "Requirements",
            drive_id: "drive-1",
            drive_name: "Product",
            file_id: "template-1",
            mime_type: "application/vnd.google-apps.document",
            modified_time: "2026-08-02T12:00:00Z",
            name: "PRD",
          },
        ],
      }),
    );

    await expect(smartCreateApi.listTemplates(true)).resolves.toEqual([
      {
        categoryPath: ["Product", "PRDs"],
        description: "Requirements",
        driveId: "drive-1",
        driveName: "Product",
        fileId: "template-1",
        mimeType: "application/vnd.google-apps.document",
        modifiedTime: "2026-08-02T12:00:00Z",
        name: "PRD",
      },
    ]);
    const request = z.instanceof(Request).parse(vi.mocked(fetch).mock.calls[0]?.[0]);
    expect(request).toMatchObject({
      credentials: "include",
      method: "GET",
      url: "http://localhost:8000/templates?refresh=true",
    });
  });

  it("sends the idempotency key with the normalized creation plan", async () => {
    actionMocks.create.mockResolvedValue({
      data: JSON.stringify({
        file: {
          can_edit: true,
          drive_id: "drive-1",
          id: "file-1",
          mime_type: "application/vnd.google-apps.document",
          modified_time: null,
          name: "Launch brief",
          parent_folder_id: "folder-1",
        },
        task_id: "task-1",
        warnings: [],
      }),
      ok: true,
    });

    await smartCreateApi.create(
      {
        alias: "launch-brief",
        destination: {
          driveId: "drive-1",
          driveName: "Product",
          folderId: "folder-1",
          folderPath: ["Plans"],
        },
        emoji: "🚀",
        name: "Launch brief",
        source: { fileType: "document", kind: "blank" },
      },
      "55555555-5555-4555-8555-555555555555",
    );

    expect(actionMocks.create).toHaveBeenCalledWith(
      JSON.stringify({
        alias: "launch-brief",
        emoji: "🚀",
        name: "Launch brief",
        parent_folder_id: "folder-1",
        source: { file_type: "document", kind: "blank" },
      }),
      "55555555-5555-4555-8555-555555555555",
    );
  });

  it("rejects crossed fields in a discriminated source", async () => {
    actionMocks.propose.mockResolvedValue({
      data: JSON.stringify({
        alias: "launch-brief",
        destination: {
          drive_id: "drive-1",
          drive_name: "Product",
          folder_id: "folder-1",
          folder_path: ["Product"],
        },
        emoji: "🚀",
        name: "Launch brief",
        source: {
          file_type: "document",
          kind: "blank",
          template_file_id: "illegal-template-field",
        },
      }),
      ok: true,
    });

    await expect(
      smartCreateApi.propose({ description: "A launch brief", fileType: "document" }),
    ).rejects.toThrow("Unrecognized key");
  });
});
