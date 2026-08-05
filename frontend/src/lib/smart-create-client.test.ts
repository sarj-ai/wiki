import { afterEach, describe, expect, it, vi } from "vitest";

import { smartCreateApi } from "@/lib/smart-create-client";

const actionMocks = vi.hoisted(() => ({
  create: vi.fn(),
  getTask: vi.fn(),
  listTemplates: vi.fn(),
  propose: vi.fn(),
}));

vi.mock("@/app/actions/smart-create-actions", () => ({
  createSmartFileAction: actionMocks.create,
  getSmartCreateTaskAction: actionMocks.getTask,
  listSmartTemplatesAction: actionMocks.listTemplates,
  proposeSmartFileAction: actionMocks.propose,
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("smartCreateApi", () => {
  it("transforms Drive templates at the transport boundary", async () => {
    actionMocks.listTemplates.mockResolvedValue({
      data: JSON.stringify({
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
      ok: true,
    });

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
    expect(actionMocks.listTemplates).toHaveBeenCalledWith(true);
  });

  it("sends the idempotency key with the normalized creation plan", async () => {
    actionMocks.create.mockResolvedValue({
      data: JSON.stringify({
        attempt_count: 0,
        created_at: "2026-08-02T12:00:00Z",
        error_code: null,
        error_detail: null,
        finished_at: null,
        id: "55555555-5555-4555-8555-555555555555",
        kind: "file.smart_create",
        resource_id: null,
        result: null,
        started_at: null,
        status: "pending",
      }),
      ok: true,
    });

    await expect(
      smartCreateApi.create(
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
      ),
    ).resolves.toEqual({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });

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

  it("polls typed task states through server actions until success", async () => {
    const taskId = "55555555-5555-4555-8555-555555555555";
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify({
        attempt_count: 1,
        created_at: "2026-08-02T12:00:00Z",
        error_code: null,
        error_detail: null,
        finished_at: "2026-08-02T12:00:02Z",
        id: taskId,
        kind: "file.smart_create",
        resource_id: "file-1",
        result: {
          file: {
            can_add_children: false,
            can_edit: true,
            description: "Created from the selected Drive template",
            drive_id: "drive-1",
            id: "file-1",
            mime_type: "application/vnd.google-apps.document",
            modified_time: null,
            name: "Launch brief",
            parent_folder_id: "folder-1",
          },
          task_id: taskId,
          warnings: [],
        },
        started_at: "2026-08-02T12:00:01Z",
        status: "succeeded",
      }),
      ok: true,
    });

    await expect(
      smartCreateApi.waitForCompletion({ id: taskId, status: "pending" }, undefined, 0),
    ).resolves.toMatchObject({ file: { id: "file-1" }, taskId });
    expect(actionMocks.getTask).toHaveBeenCalledWith(taskId);
  });

  it("surfaces typed terminal task failures", async () => {
    await expect(
      smartCreateApi.waitForCompletion({
        errorCode: "template_changed",
        errorDetail: "Template changed in Drive",
        id: "55555555-5555-4555-8555-555555555555",
        status: "failed",
      }),
    ).rejects.toThrow("Template changed in Drive");
  });
});
