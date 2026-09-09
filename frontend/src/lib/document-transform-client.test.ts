// @vitest-environment happy-dom

const actionMocks = vi.hoisted(() => ({
  capabilities: vi.fn(),
  create: vi.fn(),
  getTask: vi.fn(),
}));

vi.mock("@/lib/document-transform-http", () => ({
  transformHttp: (path: string, body?: string, key?: string) => {
    if (path === "/capabilities") return actionMocks.capabilities();
    if (path.startsWith("/tasks/")) return actionMocks.getTask(path.slice("/tasks/".length));
    return actionMocks.create(body, key);
  },
}));

import { documentTransformApi } from "@/lib/document-transform-client";
import { DocumentTransformNeedsReviewError } from "@/lib/document-transform-types";
const taskId = "55555555-5555-4555-8555-555555555555";

describe("documentTransformApi", () => {
  it("keeps an incomplete-copy link on a terminal failure without promoting a result", async () => {
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify({
        id: taskId,
        status: "failed",
        error_code: "output_verification_failed",
        error_detail: "Task could not be completed",
        unverified_output: { file_id: "known-copy" },
      }),
      ok: true,
    });
    await expect(
      documentTransformApi.waitForCompletion({ id: taskId, status: "running" }, undefined, 0),
    ).rejects.toMatchObject({
      name: "DocumentTransformTerminalError",
      unverifiedOutputId: "known-copy",
      code: "output_verification_failed",
    });
  });
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("parses the server-gated in-place capability", async () => {
    actionMocks.capabilities.mockResolvedValue({
      data: JSON.stringify({ copy: true, in_place: true }),
      ok: true,
    });

    await expect(documentTransformApi.capabilities()).resolves.toEqual({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
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
        output_strategy: "copy",
        source: {
          expected_modified_time: "2026-09-03T12:00:00Z",
          file_id: "doc-1",
          kind: "drive_file",
        },
      }),
      "66666666-6666-4666-8666-666666666666",
    );
  });

  it("marks a retained task returned by a replayed submission", async () => {
    actionMocks.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
      replayed: true,
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
    ).resolves.toEqual({ id: taskId, replayed: true, status: "pending" });
  });

  it("sends the explicit copy name without changing the source", async () => {
    actionMocks.create.mockResolvedValue({ data: JSON.stringify(task("pending")), ok: true });
    await documentTransformApi.create(
      {
        kind: "drive_file",
        fileId: "doc-1",
        name: "Source",
        expectedModifiedTime: "2026-09-03T12:00:00Z",
      },
      "format",
      taskId,
      "copy",
      "Architecture decisions",
    );
    expect(JSON.parse(actionMocks.create.mock.calls[0]?.[0] as string)).toMatchObject({
      output_name: "Architecture decisions",
      output_strategy: "copy",
      source: { file_id: "doc-1" },
    });
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
      output_strategy: "copy",
      source: {
        content: "# Launch",
        destination_folder_id: "folder-1",
        kind: "markdown",
        name: "Launch brief",
      },
    });
  });

  it("sends in-place as an explicit request strategy", async () => {
    actionMocks.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await documentTransformApi.create(
      {
        expectedModifiedTime: "2026-09-03T12:00:00Z",
        fileId: "doc-1",
        kind: "drive_file",
        name: "Source",
      },
      "both",
      "66666666-6666-4666-8666-666666666666",
      "in_place",
    );

    expect(JSON.parse(actionMocks.create.mock.calls[0]?.[0] as string)).toMatchObject({
      output_name: null,
      output_strategy: "in_place",
    });
  });

  it.each([undefined, 0, 1, 20])("parses heading count %s while polling", async (count) => {
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify({
        ...task("succeeded"),
        result: {
          access_scope: "inherited",
          applied_patch_count: 1,
          promoted_heading_count: count,
          artifact_kind: "presentation",
          findings: [
            {
              block_id: "paragraph-1",
              code: "native_rich_text_preserved",
              detail: "Rich text was preserved",
              severity: "informational",
            },
          ],
          mode: "unslop",
          model_ids: ["gemini-flash"],
          needs_review: false,
          operation_id: taskId,
          output: {
            file_id: "output-1",
            media_type: "application/vnd.google-apps.presentation",
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
      promotedHeadingCount: count ?? 0,
      accessScope: "inherited",
      artifactKind: "presentation",
      findings: [
        {
          blockId: "paragraph-1",
          code: "native_rich_text_preserved",
          detail: "Rich text was preserved",
          severity: "informational",
        },
      ],
      outputFile: {
        id: "output-1",
        mimeType: "application/vnd.google-apps.presentation",
        name: "Source — Unslopped",
      },
      sourceSha256: "a".repeat(64),
      validation: { sourceUnchanged: true },
    });
    expect(actionMocks.getTask).toHaveBeenCalledWith(taskId);
  });

  it("fails closed when a legacy review finding contradicts needs_review", async () => {
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify({
        ...task("succeeded"),
        result: {
          access_scope: "inherited",
          applied_patch_count: 0,
          findings: [
            {
              block_id: null,
              code: "legacy_warning",
              detail: "Review the preserved structure.",
            },
          ],
          mode: "format",
          model_ids: [],
          needs_review: false,
          operation_id: taskId,
          output: {
            file_id: "output-1",
            name: "Review",
            parent_folder_id: "folder-1",
            web_url: "https://docs.google.com/document/d/output-1/edit",
          },
          pipeline_version: "sarj-v1",
          source_sha256: "a".repeat(64),
          tool_versions: {},
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
    ).rejects.toMatchObject({
      name: "DocumentTransformNeedsReviewError",
      result: {
        needsReview: true,
        findings: [{ code: "legacy_warning", severity: "review" }],
      },
    });
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

  it("replaces a redacted review detail with safe, actionable guidance", async () => {
    await expect(
      documentTransformApi.waitForCompletion({
        errorCode: "needs_review",
        errorDetail: "Task could not be completed",
        id: taskId,
        status: "failed",
      }),
    ).rejects.toEqual(
      new DocumentTransformNeedsReviewError(
        "Sarj stopped at a safety check. Review the document and its Drive version history before trying again.",
      ),
    );
  });

  it("uses code-specific guidance when a terminal task has no detail", async () => {
    await expect(
      documentTransformApi.waitForCompletion({
        errorCode: "source_changed",
        id: taskId,
        status: "failed",
      }),
    ).rejects.toMatchObject({
      code: "source_changed",
      message:
        "The document changed after polishing started. Reload and review the latest version before trying again.",
      name: "DocumentTransformTerminalError",
    });
  });

  it("truthfully identifies an original that may have changed after verification", async () => {
    await expect(
      documentTransformApi.waitForCompletion({
        errorCode: "in_place_verification_failed",
        errorDetail: "Task could not be completed",
        id: taskId,
        status: "failed",
        unverifiedOutputId: "original-document",
      }),
    ).rejects.toMatchObject({
      code: "in_place_verification_failed",
      message: expect.stringContaining("original document may have changed"),
      name: "DocumentTransformTerminalError",
      unverifiedOutputId: "original-document",
    });
  });

  it("stops polling instead of waiting forever", async () => {
    actionMocks.getTask.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await expect(
      documentTransformApi.waitForCompletion({ id: taskId, status: "running" }, undefined, 0, 1),
    ).rejects.toThrow("Document polish timed out; check its task status before retrying");
    expect(actionMocks.getTask).toHaveBeenCalledTimes(1);
  });

  it("retries transient task reads without starting a second transform", async () => {
    actionMocks.getTask
      .mockResolvedValueOnce({ error: "temporarily unavailable", ok: false, status: 503 })
      .mockResolvedValueOnce({
        data: JSON.stringify({
          ...task("succeeded"),
          result: {
            access_scope: "inherited",
            applied_patch_count: 0,
            artifact_kind: "document",
            brand_profile_id: "sarj-2026-v4",
            brand_profile_sha256: "b".repeat(64),
            changed: false,
            deterministic_patch_count: 0,
            findings: [],
            llm_invoked: false,
            mode: "format",
            model_ids: [],
            model_patch_count: 0,
            needs_review: false,
            operation_id: taskId,
            output: {
              file_id: "doc-1",
              media_type: "application/vnd.google-apps.document",
              name: "Source",
              parent_folder_id: "folder-1",
              web_url: null,
            },
            output_revision_id: "revision-2",
            output_strategy: "in_place",
            pipeline_version: "document-transform-v3",
            source_revision_id: "revision-1",
            source_sha256: "a".repeat(64),
            short_link_alias: "architecture-decisions",
            short_link_status: "ready",
            tool_versions: {},
            validation: {
              protected_content_preserved: true,
              source_unchanged: false,
              structure_preserved: true,
              unsafe_patch_count: 0,
            },
          },
        }),
        ok: true,
      });

    await expect(
      documentTransformApi.waitForCompletion({ id: taskId, status: "running" }, undefined, 0, 3),
    ).resolves.toMatchObject({
      outputStrategy: "in_place",
      outputFile: { id: "doc-1" },
      shortLinkAlias: "architecture-decisions",
      shortLinkStatus: "ready",
    });
    expect(actionMocks.getTask).toHaveBeenCalledTimes(2);
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
