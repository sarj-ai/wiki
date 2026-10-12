// @vitest-environment happy-dom

import type { ActionResult } from "@/lib/action-result";
import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";

const ACTION_MOCKS = vi.hoisted(() => ({
  capabilities: vi.fn<() => Promise<ActionResult<string>>>(),
  create: vi.fn<(body?: string, key?: string) => Promise<ActionResult<string>>>(),
  current: vi.fn<(path: string) => Promise<ActionResult<string>>>(),
  getTask: vi.fn<(id: string) => Promise<ActionResult<string>>>(),
}));

vi.mock("@/lib/clients/transform-http", () => ({
  transformHttp: async (path: string, body?: string, key?: string) => {
    if (path === "/capabilities") return ACTION_MOCKS.capabilities();
    if (path.startsWith("/current?")) return ACTION_MOCKS.current(path);
    if (path.startsWith("/tasks/")) return ACTION_MOCKS.getTask(path.slice("/tasks/".length));
    return ACTION_MOCKS.create(body, key);
  },
}));

import { DOCUMENT_TRANSFORM_API } from "@/lib/document-transform-api";
import { DocumentTransformNeedsReviewError } from "@/lib/document-transform-types";
const TASK_ID = "55555555-5555-4555-8555-555555555555";
const UPLOAD_FIELDS = { expiresInSeconds: "expires_in_seconds", id: "upload_id" } as const;

describe("documentTransformApi", () => {
  it("does not poll as the replacement session after the original owner retires during sleep", async () => {
    vi.useFakeTimers();
    const owner = {};
    const next = {};
    claimBrowserSession(owner);
    ACTION_MOCKS.getTask.mockResolvedValue({
      data: JSON.stringify({
        error_code: "stopped",
        error_detail: null,
        id: TASK_ID,
        status: "failed",
      }),
      ok: true,
    });
    const pending = DOCUMENT_TRANSFORM_API.waitForCompletion({
      id: TASK_ID,
      status: "running",
    }).catch((error: unknown) => error);
    try {
      retireBrowserSession(owner);
      claimBrowserSession(next);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(ACTION_MOCKS.getTask).not.toHaveBeenCalled();
      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    } finally {
      retireBrowserSession(next);
      vi.useRealTimers();
      await pending;
    }
  });

  it("keeps an incomplete-copy link on a terminal failure without promoting a result", async () => {
    ACTION_MOCKS.getTask.mockResolvedValue({
      data: JSON.stringify({
        id: TASK_ID,
        status: "failed",
        error_code: "output_verification_failed",
        error_detail: "Task could not be completed",
        unverified_output: { file_id: "known-copy" },
      }),
      ok: true,
    });
    await expect(
      DOCUMENT_TRANSFORM_API.waitForCompletion({ id: TASK_ID, status: "running" }, undefined, 0),
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
    ACTION_MOCKS.capabilities.mockResolvedValue({
      data: JSON.stringify({ copy: true, in_place: true }),
      ok: true,
    });

    await expect(DOCUMENT_TRANSFORM_API.capabilities()).resolves.toEqual({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
  });

  it("returns the exact current-revision polish receipt", async () => {
    ACTION_MOCKS.current.mockResolvedValue({
      data: JSON.stringify({ result: currentResult() }),
      ok: true,
    });

    await expect(DOCUMENT_TRANSFORM_API.current("document 1", "format")).resolves.toMatchObject({
      mode: "format",
      operationId: TASK_ID,
      outputRevisionId: "revision-current",
      outputStrategy: "in_place",
    });
    expect(ACTION_MOCKS.current).toHaveBeenCalledWith("/current?file_id=document%201&mode=format");
  });

  it("returns null when the current revision has not been polished", async () => {
    ACTION_MOCKS.current.mockResolvedValue({ data: JSON.stringify({ result: null }), ok: true });

    await expect(DOCUMENT_TRANSFORM_API.current("document-1", "both")).resolves.toBeNull();
  });

  it("serializes a revision-locked Drive source and creates no in-place output", async () => {
    ACTION_MOCKS.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await expect(
      DOCUMENT_TRANSFORM_API.create(
        {
          expectedModifiedTime: "2026-09-03T12:00:00Z",
          fileId: "doc-1",
          kind: "drive_file",
          name: "Source",
        },
        "both",
        "66666666-6666-4666-8666-666666666666",
      ),
    ).resolves.toEqual({ id: TASK_ID, status: "pending" });

    expect(ACTION_MOCKS.create).toHaveBeenCalledWith(
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
    ACTION_MOCKS.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
      replayed: true,
    });

    await expect(
      DOCUMENT_TRANSFORM_API.create(
        {
          expectedModifiedTime: "2026-09-03T12:00:00Z",
          fileId: "doc-1",
          kind: "drive_file",
          name: "Source",
        },
        "both",
        "66666666-6666-4666-8666-666666666666",
      ),
    ).resolves.toEqual({ id: TASK_ID, replayed: true, status: "pending" });
  });

  it("sends the explicit copy name without changing the source", async () => {
    ACTION_MOCKS.create.mockResolvedValue({ data: JSON.stringify(task("pending")), ok: true });
    await DOCUMENT_TRANSFORM_API.create(
      {
        kind: "drive_file",
        fileId: "doc-1",
        name: "Source",
        expectedModifiedTime: "2026-09-03T12:00:00Z",
      },
      "format",
      TASK_ID,
      "copy",
      "Architecture decisions",
    );
    expect(JSON.parse(ACTION_MOCKS.create.mock.calls[0]?.[0] ?? "")).toMatchObject({
      output_name: "Architecture decisions",
      output_strategy: "copy",
      source: { file_id: "doc-1" },
    });
  });

  it("serializes pasted Markdown with its explicit destination", async () => {
    ACTION_MOCKS.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await DOCUMENT_TRANSFORM_API.create(
      {
        content: "# Launch",
        destinationFolderId: "folder-1",
        kind: "markdown",
        name: "Launch brief",
      },
      "format",
      "77777777-7777-4777-8777-777777777777",
    );

    expect(JSON.parse(ACTION_MOCKS.create.mock.calls[0]?.[0] ?? "")).toMatchObject({
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

  it("uploads file bytes to GCS and submits only the upload reference", async () => {
    const uploadId = "77777777-7777-4777-8777-777777777777";
    ACTION_MOCKS.create.mockResolvedValueOnce({
      data: JSON.stringify({
        upload_id: uploadId,
        url: "https://storage.googleapis.com/bucket/object?signature=secret",
        method: "PUT",
        headers: { "Content-Type": "text/markdown", "x-goog-if-generation-match": "0" },
        expires_in_seconds: 600,
      }),
      ok: true,
    });
    ACTION_MOCKS.create.mockResolvedValueOnce({ data: JSON.stringify(task("pending")), ok: true });
    const put = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 200 }));
    const file = new File(["# Launch"], "launch.md", { type: "text/markdown" });

    await expect(DOCUMENT_TRANSFORM_API.uploadMarkdown(file)).resolves.toBe(uploadId);
    await DOCUMENT_TRANSFORM_API.create(
      { kind: "uploaded_markdown", uploadId, name: "Launch", destinationFolderId: "folder" },
      "format",
      TASK_ID,
    );

    expect(put).toHaveBeenCalledWith(
      "https://storage.googleapis.com/bucket/object?signature=secret",
      expect.objectContaining({ body: file, method: "PUT" }),
    );
    expect(JSON.parse(ACTION_MOCKS.create.mock.calls[1]?.[0] ?? "")).toMatchObject({
      source: {
        kind: "uploaded_markdown",
        upload_id: uploadId,
        name: "Launch",
        destination_folder_id: "folder",
      },
    });
    put.mockRestore();
  });

  it.each([
    { status: 200, expected: "77777777-7777-4777-8777-777777777777" },
    { status: 503, expected: "Markdown upload failed. Choose the file again to retry." },
  ])("releases the unused Markdown PUT body on HTTP $status", async ({ status, expected }) => {
    const uploadId = "77777777-7777-4777-8777-777777777777";
    ACTION_MOCKS.create.mockResolvedValueOnce({
      data: JSON.stringify({
        [UPLOAD_FIELDS.id]: uploadId,
        url: "https://storage.googleapis.com/bucket/object?signature=fixture",
        method: "PUT",
        headers: { "Content-Type": "text/markdown" },
        [UPLOAD_FIELDS.expiresInSeconds]: 600,
      }),
      ok: true,
    });
    const cleanup = Promise.withResolvers<undefined>();
    const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
    const put = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(new ReadableStream<Uint8Array>({ cancel }), { status }));
    const file = new File(["# Launch"], "launch.md", { type: "text/markdown" });
    try {
      const pending = DOCUMENT_TRANSFORM_API.uploadMarkdown(file).catch((error: unknown) => {
        if (!(error instanceof Error)) throw error;
        return error.message;
      });
      await expect(pending).resolves.toBe(expected);
      expect(cancel).toHaveBeenCalledOnce();
      expect(put).toHaveBeenCalledOnce();
    } finally {
      cleanup.resolve(undefined);
      put.mockRestore();
    }
  });

  it("sends in-place as an explicit request strategy", async () => {
    ACTION_MOCKS.create.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await DOCUMENT_TRANSFORM_API.create(
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

    expect(JSON.parse(ACTION_MOCKS.create.mock.calls[0]?.[0] ?? "")).toMatchObject({
      output_name: null,
      output_strategy: "in_place",
    });
  });

  it.each([undefined, 0, 1, 20])("parses heading count %s while polling", async (count) => {
    ACTION_MOCKS.getTask.mockResolvedValue({
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
          operation_id: TASK_ID,
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
      DOCUMENT_TRANSFORM_API.waitForCompletion({ id: TASK_ID, status: "running" }, undefined, 0),
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
    expect(ACTION_MOCKS.getTask).toHaveBeenCalledWith(TASK_ID);
  });

  it("fails closed when a legacy review finding contradicts needs_review", async () => {
    ACTION_MOCKS.getTask.mockResolvedValue({
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
          operation_id: TASK_ID,
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
      DOCUMENT_TRANSFORM_API.waitForCompletion({ id: TASK_ID, status: "running" }, undefined, 0),
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
      DOCUMENT_TRANSFORM_API.waitForCompletion({
        errorCode: "needs_review",
        errorDetail: "A table could not be preserved safely.",
        id: TASK_ID,
        status: "failed",
      }),
    ).rejects.toEqual(
      new DocumentTransformNeedsReviewError("A table could not be preserved safely."),
    );
  });

  it("replaces a redacted review detail with safe, actionable guidance", async () => {
    await expect(
      DOCUMENT_TRANSFORM_API.waitForCompletion({
        errorCode: "needs_review",
        errorDetail: "Task could not be completed",
        id: TASK_ID,
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
      DOCUMENT_TRANSFORM_API.waitForCompletion({
        errorCode: "source_changed",
        id: TASK_ID,
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
    const result = DOCUMENT_TRANSFORM_API.waitForCompletion({
      errorCode: "in_place_verification_failed",
      errorDetail: "Task could not be completed",
      id: TASK_ID,
      status: "failed",
      unverifiedOutputId: "original-document",
    });
    await expect(result).rejects.toMatchObject({
      code: "in_place_verification_failed",
      name: "DocumentTransformTerminalError",
      unverifiedOutputId: "original-document",
    });
    await expect(result).rejects.toThrow("original document may have changed");
  });

  it("stops polling instead of waiting forever", async () => {
    ACTION_MOCKS.getTask.mockResolvedValue({
      data: JSON.stringify(task("pending")),
      ok: true,
    });

    await expect(
      DOCUMENT_TRANSFORM_API.waitForCompletion({ id: TASK_ID, status: "running" }, undefined, 0, 1),
    ).rejects.toThrow("Document polish timed out; check its task status before retrying");
    expect(ACTION_MOCKS.getTask).toHaveBeenCalledOnce();
  });

  it("retries transient task reads without starting a second transform", async () => {
    ACTION_MOCKS.getTask
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
            operation_id: TASK_ID,
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
      DOCUMENT_TRANSFORM_API.waitForCompletion({ id: TASK_ID, status: "running" }, undefined, 0, 3),
    ).resolves.toMatchObject({
      outputStrategy: "in_place",
      outputFile: { id: "doc-1" },
      shortLinkAlias: "architecture-decisions",
      shortLinkStatus: "ready",
    });
    expect(ACTION_MOCKS.getTask).toHaveBeenCalledTimes(2);
  });
});

function task(status: "pending" | "succeeded") {
  return {
    attempt_count: status === "pending" ? 0 : 1,
    created_at: "2026-09-03T12:00:00Z",
    error_code: null,
    error_detail: null,
    finished_at: status === "pending" ? null : "2026-09-03T12:01:00Z",
    id: TASK_ID,
    kind: "file.document_transform",
    resource_id: null,
    result: null,
    started_at: status === "pending" ? null : "2026-09-03T12:00:01Z",
    status,
  };
}

function currentResult() {
  return {
    access_scope: "inherited",
    applied_patch_count: 1,
    artifact_kind: "document",
    brand_profile_id: "sarj-2026-v4",
    brand_profile_sha256: "b".repeat(64),
    changed: true,
    deterministic_patch_count: 1,
    findings: [],
    llm_invoked: false,
    mode: "format",
    model_ids: [],
    model_patch_count: 0,
    needs_review: false,
    operation_id: TASK_ID,
    output: {
      file_id: "document-1",
      media_type: "application/vnd.google-apps.document",
      name: "Decision",
      parent_folder_id: "folder-1",
      web_url: "https://docs.google.com/document/d/document-1/edit",
    },
    output_revision_id: "revision-current",
    output_strategy: "in_place",
    pipeline_version: "document-transform-v3",
    source_revision_id: "revision-source",
    source_sha256: "a".repeat(64),
    tool_versions: { format: "v1" },
    validation: {
      protected_content_preserved: true,
      source_unchanged: false,
      structure_preserved: true,
      unsafe_patch_count: 0,
    },
  };
}
