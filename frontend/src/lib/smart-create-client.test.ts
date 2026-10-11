import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";
import { smartCreateApi } from "@/lib/smart-create-client";

const ACTION_MOCKS = vi.hoisted(() => ({ create: vi.fn(), propose: vi.fn() }));
const FETCH_MOCK = vi.fn<typeof fetch>();
const TASK_ID = "55555555-5555-4555-8555-555555555555";
const CATALOG = {
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
};
const PENDING_TASK = {
  attempt_count: 0,
  created_at: "2026-08-02T12:00:00Z",
  error_code: null,
  error_detail: null,
  finished_at: null,
  id: TASK_ID,
  kind: "file.smart_create",
  resource_id: null,
  result: null,
  started_at: null,
  status: "pending",
};
const SUCCEEDED_TASK = {
  ...PENDING_TASK,
  attempt_count: 1,
  finished_at: "2026-08-02T12:00:02Z",
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
    task_id: TASK_ID,
    warnings: [],
  },
  started_at: "2026-08-02T12:00:01Z",
  status: "succeeded",
};

vi.mock("@/app/actions/smart-create-actions", () => ({
  createSmartFileAction: ACTION_MOCKS.create,
  proposeSmartFileAction: ACTION_MOCKS.propose,
}));

beforeEach(() => {
  vi.clearAllMocks();
  FETCH_MOCK.mockReset();
  vi.stubGlobal("fetch", FETCH_MOCK);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("smartCreateApi", () => {
  it("does not poll an old task using the replacement session after a sleep", async () => {
    vi.useFakeTimers();
    const owner = {};
    const next = {};
    claimBrowserSession(owner);
    FETCH_MOCK.mockResolvedValue(
      Response.json({
        ...PENDING_TASK,
        error_code: "stopped",
        error_detail: null,
        status: "failed",
      }),
    );
    const pending = smartCreateApi
      .waitForCompletion({
        id: TASK_ID,
        status: "pending",
      })
      .catch((error: unknown) => error);
    try {
      retireBrowserSession(owner);
      claimBrowserSession(next);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(FETCH_MOCK).not.toHaveBeenCalled();
      await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    } finally {
      retireBrowserSession(next);
      vi.useRealTimers();
      await pending;
    }
  });

  it("transforms and refreshes authenticated Drive templates over GET", async () => {
    FETCH_MOCK.mockResolvedValue(Response.json(CATALOG));

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
    const [url, options] = FETCH_MOCK.mock.calls[0] ?? [];
    expect(url).toBe("/api/wiki/templates?refresh=true");
    expect(options?.headers).toMatchObject({ "x-wiki-read": "1" });
    expect(options?.cache).toBe("no-store");
  });

  it("starts task polling and catalog reads concurrently", async () => {
    const gate = Promise.withResolvers<undefined>();
    FETCH_MOCK.mockImplementation(async (input) => {
      await gate.promise;
      const url = typeof input === "string" ? input : "href" in input ? input.href : input.url;
      return Response.json(url.includes("/templates") ? CATALOG : PENDING_TASK);
    });

    const task = smartCreateApi.getTask(TASK_ID);
    const catalog = smartCreateApi.listTemplates();

    expect(FETCH_MOCK).toHaveBeenCalledTimes(2);
    gate.resolve(undefined);
    await expect(task).resolves.toEqual({ id: TASK_ID, status: "pending" });
    await expect(catalog).resolves.toHaveLength(1);
  });

  it("cancels an in-flight task read instead of waiting for its old result", async () => {
    const controller = new AbortController();
    FETCH_MOCK.mockImplementation(
      async (_input, options) =>
        await new Promise<Response>((_resolve, reject) => {
          options?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted", "AbortError")),
            {
              once: true,
            },
          );
        }),
    );
    const pending = smartCreateApi.getTask(TASK_ID, controller.signal);
    const outcome = pending.catch((error: unknown) => error);

    controller.abort();

    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
    expect(FETCH_MOCK.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("rejects malformed task reads at the transport boundary", async () => {
    FETCH_MOCK.mockResolvedValue(Response.json({ ...PENDING_TASK, status: "private-status" }));

    await expect(smartCreateApi.getTask(TASK_ID)).rejects.toThrow();
  });

  it("sends the idempotency key with the normalized creation plan", async () => {
    ACTION_MOCKS.create.mockResolvedValue({ data: JSON.stringify(PENDING_TASK), ok: true });

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
        TASK_ID,
      ),
    ).resolves.toEqual({ id: TASK_ID, status: "pending" });

    expect(ACTION_MOCKS.create).toHaveBeenCalledWith(
      JSON.stringify({
        alias: "launch-brief",
        emoji: "🚀",
        name: "Launch brief",
        parent_folder_id: "folder-1",
        source: { file_type: "document", kind: "blank" },
      }),
      TASK_ID,
    );
  });

  it("rejects crossed fields in a discriminated source", async () => {
    ACTION_MOCKS.propose.mockResolvedValue({
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

  it("polls typed GET task states until success", async () => {
    FETCH_MOCK.mockResolvedValue(Response.json(SUCCEEDED_TASK));

    await expect(
      smartCreateApi.waitForCompletion({ id: TASK_ID, status: "pending" }, undefined, 0),
    ).resolves.toMatchObject({ file: { id: "file-1" }, taskId: TASK_ID });
    const [url] = FETCH_MOCK.mock.calls[0] ?? [];
    expect(url).toBe(`/api/wiki/tasks/${TASK_ID}`);
  });

  it("surfaces typed terminal task failures", async () => {
    await expect(
      smartCreateApi.waitForCompletion({
        errorCode: "template_changed",
        errorDetail: "Template changed in Drive",
        id: TASK_ID,
        status: "failed",
      }),
    ).rejects.toThrow("Template changed in Drive");
  });
});
