// @vitest-environment happy-dom

const actionMocks = vi.hoisted(() => ({
  create: vi.fn(),
  getTask: vi.fn(),
}));

vi.mock("@/app/actions/smart-create-actions", () => ({
  createDocumentTransformAction: actionMocks.create,
  getSmartCreateTaskAction: actionMocks.getTask,
}));

import { documentTransformApi } from "@/lib/document-transform-client";
import { DocumentTransformNeedsReviewError } from "@/lib/document-transform-types";
const taskId = "55555555-5555-4555-8555-555555555555";

describe("documentTransformApi", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("serializes a revision-locked Drive source and creates no in-place output", async () => {
    actionMocks.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await expect(
      documentTransformApi.create(
        {
          expectedModifiedTime: "2026-09-03T12:00:00Z",
          fileId: "doc-1",
          kind: "drive_file",
          name: "Source",
        },
        "both",
        "66666666-6666-4666-8666-666666666666",
      ),
    ).resolves.toEqual({ id: taskId, status: "pending" });

    expect(actionMocks.create).toHaveBeenCalledWith(
      JSON.stringify({
        mode: "both",
        output_name: null,
        source: {
          expected_modified_time: "2026-09-03T12:00:00Z",
          file_id: "doc-1",
          kind: "drive_file",
        },
      }),
      "66666666-6666-4666-8666-666666666666",
    );
  });

  it("serializes pasted Markdown with its explicit destination", async () => {
    actionMocks.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await documentTransformApi.create(
      {
        content: "# Launch",
        destinationFolderId: "folder-1",
        kind: "markdown",
        name: "Launch brief",
      },
      "format",
      "77777777-7777-4777-8777-777777777777",
    );

    expect(JSON.parse(actionMocks.create.mock.calls[0]?.[0] as string)).toMatchObject({
      mode: "format",
      output_name: null,
      source: {
        content: "# Launch",
        destination_folder_id: "folder-1",
        kind: "markdown",
        name: "Launch brief",
      },
    });
  });

  it("polls until the typed transformed file is ready", async () => {
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify({
        ...task("succeeded"),
        result: {
          applied_patch_count: 1,
          findings: [{ block_id: "paragraph-1", code: "ai_phrase", detail: "Removed filler" }],
          mode: "unslop",
          model_ids: ["gemini-flash"],
          needs_review: false,
          operation_id: taskId,
          output: {
            file_id: "output-1",
            name: "Source — Unslopped",
            parent_folder_id: "folder-1",
            web_url: "https://docs.google.com/document/d/output-1/edit",
          },
          pipeline_version: "sarj-v1",
          source_sha256: "a".repeat(64),
          tool_versions: { unslop: "d81f519" },
          validation: {
            protected_content_preserved: true,
            source_unchanged: true,
            structure_preserved: true,
            unsafe_patch_count: 0,
          },
        },
      }),
      ok: true,
    });

    await expect(
      documentTransformApi.waitForCompletion({ id: taskId, status: "running" }, undefined, 0),
    ).resolves.toMatchObject({
      mode: "unslop",
      outputFile: { id: "output-1", name: "Source — Unslopped" },
      sourceSha256: "a".repeat(64),
      validation: { sourceUnchanged: true },
    });
    expect(actionMocks.getTask).toHaveBeenCalledWith(taskId);
  });

  it("surfaces the review gate as a distinct outcome", async () => {
    await expect(
      documentTransformApi.waitForCompletion({
        errorCode: "needs_review",
        errorDetail: "A table could not be preserved safely.",
        id: taskId,
        status: "failed",
      }),
    ).rejects.toEqual(
      new DocumentTransformNeedsReviewError("A table could not be preserved safely."),
    );
  });

  it("stops polling instead of waiting forever", async () => {
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await expect(
      documentTransformApi.waitForCompletion({ id: taskId, status: "running" }, undefined, 0, 1),
    ).rejects.toThrow("Document polish timed out; the source remains unchanged");
    expect(actionMocks.getTask).toHaveBeenCalledTimes(1);
  });
});

function task(status: "pending" | "succeeded") {
  return {
    attempt_count: status === "pending" ? 0 : 1,
    created_at: "2026-09-03T12:00:00Z",
    error_code: null,
    error_detail: null,
    finished_at: status === "pending" ? null : "2026-09-03T12:01:00Z",
    id: taskId,
    kind: "file.document_transform",
    resource_id: null,
    result: null,
    started_at: status === "pending" ? null : "2026-09-03T12:00:01Z",
    status,
  };
}
