import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";

import * as wikiActions from "@/app/actions/wiki-actions";
import { ApiError } from "@/lib/api-error";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { DOCUMENT_TRANSFORM_API } from "@/lib/document-transform-api";
import { writeTransformRecovery } from "@/lib/document-transform-recovery";
import {
  DocumentTransformNeedsReviewError,
  type DocumentTransformResult,
  DocumentTransformTerminalError,
} from "@/lib/document-transform-types";
import { makeMarkdownRecovery, saveMarkdownRecovery } from "@/lib/markdown-transform-recovery";
import { preservedInPlaceOutcome } from "@/lib/preserved-in-place-outcome";
import { installLocalStorage } from "@/test-stubs/install-local-storage";
import { MIME_DOC, MIME_DOCX, MIME_SLIDE } from "@/types/wiki";

import { DocumentPolishDialog, type DocumentPolishSource } from "./document-polish-dialog";

const NavigationMock = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => NavigationMock }));
vi.mock("@/components/wiki-sidebar/folder-picker-dialog", () => ({
  // eslint-disable-next-line @typescript-eslint/naming-convention -- matches the component export
  FolderPickerDialog: () => null,
}));

describe("DocumentPolishDialog", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    vi.clearAllMocks();
    window.sessionStorage.clear();
    installLocalStorage();
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (_name: string, callback: () => unknown) => {
          await new Promise<void>((resolve) => {
            queueMicrotask(resolve);
          });
          return callback();
        },
      },
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "capabilities").mockResolvedValue({
      copy: true,
      inPlace: false,
      presentationCopy: true,
      presentationInPlace: false,
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "current").mockResolvedValue(null);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "uploadMarkdown").mockResolvedValue(
      "77777777-7777-4777-8777-777777777777",
    );
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    host.remove();
    vi.restoreAllMocks();
  });

  it("requires a secure owner and recovers after reloading the session", async () => {
    const source = {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    } as const;
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValue(new Error("offline"));
    renderDialog(root, source, undefined, null);
    expect(document.querySelector('[role="alert"]')).toHaveTextContent(
      "Secure session identity unavailable",
    );
    expect(findButtonNow("Create polished copy")).toBeDisabled();
    clickButton(findButtonNow("Create polished copy"));
    expect(create).not.toHaveBeenCalled();
    clickButton(findButtonNow("Reload session"));
    expect(NavigationMock.refresh).toHaveBeenCalledOnce();
    expect(NavigationMock.push).not.toHaveBeenCalled();
    renderDialog(root, source, undefined, "owner-1");
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    expect(document.body).not.toHaveTextContent("Secure session identity unavailable");
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => expect(create).toHaveBeenCalledOnce());
  });

  it("always asks for a mode and discloses the untouched source copy", () => {
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    expect(document.body).toHaveTextContent("Formatting");
    expect(document.body).toHaveTextContent("Unslop");
    expect(document.body).toHaveTextContent("Both");
    expect(findButtonNow("Both")).toHaveAttribute("aria-pressed", "true");
    expect(document.body).toHaveTextContent("“Architecture review” stays unchanged");
    expect(document.body).toHaveTextContent("A new Google Doc will be created next to it");
  });

  it("starts once, polls, and navigates to the verified output copy", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
    const completion =
      deferred<Awaited<ReturnType<typeof DOCUMENT_TRANSFORM_API.waitForCompletion>>>();
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    clickButton(findButtonNow("Unslop"));
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    const submit = findButtonNow("Create polished copy");
    clickButton(submit);
    clickButton(submit);

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Creating and checking the new document");
      expect(findButtonNow("Polishing…")).toBeDisabled();
    });
    expect(document.body).toHaveTextContent("Checking its status automatically.");
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
    expect(document.body).toHaveTextContent("Leaving this page does not cancel it.");
    expect(document.body).not.toHaveTextContent("Keep this window open");
    expect(document.body).toHaveTextContent(`Task: ${pending.id}`);
    expect(document.body).not.toHaveTextContent("already reserved");
    expect(document.body).not.toHaveTextContent("Resume checking");
    expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledExactlyOnceWith(
      {
        expectedModifiedTime: "2026-09-03T12:00:00Z",
        fileId: "source-1",
        kind: "drive_file",
        name: "Architecture review",
      },
      "unslop",
      expect.any(String),
      "copy",
    );
    expect(DOCUMENT_TRANSFORM_API.waitForCompletion).toHaveBeenCalledWith(
      pending,
      expect.any(AbortSignal),
    );

    completion.resolve(transformResult());

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Polish complete");
    });
    expect(NavigationMock.push).not.toHaveBeenCalled();
    expect(document.body).toHaveTextContent("choose Short link");
    clickButton(findButtonNow("Open polished document"));
    expect(NavigationMock.push).toHaveBeenCalledWith("/d/output-1");
  });

  it("completes once and shows verified preservation details without a review alert", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockResolvedValue({
      ...transformResult(),
      findings: [
        {
          blockId: "rich-1",
          code: "native_rich_text_preserved",
          detail: "Authored rich text was preserved safely.",
          severity: "informational",
        },
      ],
    });

    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    const submit = findButtonNow("Create polished copy");
    clickButton(submit);
    clickButton(submit);

    await vi.waitFor(() => expect(document.body).toHaveTextContent("Polish complete"));
    expect(document.body).toHaveTextContent("Preserved safely");
    expect(document.body).toHaveTextContent("Authored rich text was preserved safely.");
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(document.body).not.toHaveTextContent("Review notes");
    expect(create).toHaveBeenCalledOnce();
    clickButton(findButtonNow("Open polished document"));
    expect(NavigationMock.push).toHaveBeenCalledWith("/d/output-1");
  });

  it("reports a supported custom-header copy as partial branding without false review", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockResolvedValue({
      ...transformResult(),
      mode: "format",
      appliedPatchCount: 0,
      findings: [
        {
          blockId: null,
          code: "custom_furniture_preserved",
          detail:
            "Supported body formatting was applied; existing custom headers, footers, and page geometry were preserved. Complete Sarj branding was not applied.",
          severity: "informational",
        },
      ],
    });

    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => expect(document.body).toHaveTextContent("Polish complete"));
    expect(document.body).toHaveTextContent("Complete Sarj branding was not applied");
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(document.body).not.toHaveTextContent("This document needs review");
  });

  it.each(["failure", "review", "success", "no-op"] as const)(
    "reveals a new %s outcome once without disturbing the Markdown draft",
    async (outcome) => {
      const source = {
        initialDestination: {
          driveId: "drive-1",
          driveName: "Workspace",
          folderId: "folder-1",
          folderPath: ["Folder"],
        },
        kind: "markdown",
      } satisfies DocumentPolishSource;
      const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
      const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
      const completion = deferred<DocumentTransformResult>();
      vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);
      renderDialog(root, source);
      const body = document.querySelector<HTMLElement>("[data-polish-scroll-body]");
      const textarea = document.querySelector("textarea");
      if (!body || !textarea) throw new Error("Markdown dialog missing");
      const scroll = vi.spyOn(body, "scrollTo");
      const pageScroll = vi.spyOn(window, "scrollTo");
      textarea.focus();
      typeTextarea("# Draft\n\nExact original words.");
      textarea.setSelectionRange(4, 4);
      renderDialog(root, source);
      expect(document.activeElement).toBe(textarea);
      expect(textarea.selectionStart).toBe(4);
      expect(scroll).not.toHaveBeenCalled();
      body.scrollTop = 900;
      await vi.waitFor(() => expect(findButtonNow("Import Google Doc")).toBeEnabled());
      clickButton(findButtonNow("Import Google Doc"));
      await vi.waitFor(() =>
        expect(DOCUMENT_TRANSFORM_API.waitForCompletion).toHaveBeenCalledOnce(),
      );
      expect(scroll).not.toHaveBeenCalled();
      if (outcome === "failure") {
        completion.reject(
          new DocumentTransformTerminalError("Safety check failed", { code: "transform_failed" }),
        );
      } else if (outcome === "review") {
        completion.reject(
          new DocumentTransformNeedsReviewError("Review preserved dates", {
            result: { ...transformResult(), needsReview: true },
          }),
        );
      } else {
        completion.resolve({ ...transformResult(), changed: outcome === "success" });
      }
      await vi.waitFor(() => {
        expect(scroll).toHaveBeenCalledExactlyOnceWith({ top: 0, behavior: "instant" });
        expect(document.activeElement).toBe(body.querySelector("h3"));
      });
      expect(document.body).toHaveTextContent(`Task reference: ${pending.id}`);
      expect(textarea).toHaveValue("# Draft\n\nExact original words.");
      findButtonNow(outcome === "success" || outcome === "no-op" ? "Done" : "Cancel").focus();
      const focused = document.activeElement;
      renderDialog(root, source);
      expect(document.activeElement).toBe(focused);
      expect(scroll).toHaveBeenCalledOnce();
      expect(pageScroll).not.toHaveBeenCalled();
      expect(create).toHaveBeenCalledOnce();
      expect(NavigationMock.push).not.toHaveBeenCalled();
    },
  );

  it.each(["ready", "unavailable"] as const)(
    "shows the %s link outcome without repolishing",
    async (shortLinkStatus) => {
      const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
      const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
      const waitForCompletion = vi
        .spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion")
        .mockResolvedValue({
          ...transformResult(),
          changed: false,
          shortLinkAlias: shortLinkStatus === "ready" ? "architecture-review" : null,
          shortLinkStatus,
        });
      const copy = vi.fn().mockResolvedValue(undefined);
      const ensureCompletion =
        deferred<Awaited<ReturnType<typeof wikiActions.ensureDocumentAliasAction>>>();
      const ensured = {
        data: { alias: "architecture-review", fileId: "output-1" },
        ok: true,
      } as const;
      const ensure = vi
        .spyOn(wikiActions, "ensureDocumentAliasAction")
        .mockImplementation(async () => await ensureCompletion.promise);
      vi.stubGlobal("navigator", { clipboard: { writeText: copy }, locks: navigator.locks });
      try {
        renderDialog(root, {
          allowInPlace: false,
          expectedModifiedTime: "2026-09-03T12:00:00Z",
          fileId: "source-1",
          kind: "drive_file",
          mimeType: MIME_DOC,
          name: "Architecture review",
        });
        await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
        clickButton(findButtonNow("Create polished copy"));
        await vi.waitFor(() => expect(document.body).toHaveTextContent("No changes needed"));
        expect(findButtonNow("Formatting")).toBeDisabled();
        expect(document.body).toHaveTextContent("do not change who can access");
        let retryWasDisabled = false;
        if (shortLinkStatus === "ready") {
          clickButton(findButtonNow("Copy short link"));
        } else {
          const retry = findButtonNow("Retry short link");
          clickButton(retry);
          clickButton(retry);
          retryWasDisabled = findButtonNow("Creating short link…").disabled;
          ensureCompletion.resolve(ensured);
        }
        await vi.waitFor(() => expect(document.body).toHaveTextContent("Copy short link"));
        expect(copy.mock.calls).toEqual(
          shortLinkStatus === "ready" ? [[`${window.location.origin}/go/architecture-review`]] : [],
        );
        expect(document.body).toHaveTextContent("Copy short link");
        expect(retryWasDisabled).toBe(shortLinkStatus === "unavailable");
        expect(create).toHaveBeenCalledOnce();
        expect(waitForCompletion).toHaveBeenCalledOnce();
        expect(ensure).toHaveBeenCalledTimes(shortLinkStatus === "ready" ? 0 : 1);
        expect(NavigationMock.push).not.toHaveBeenCalled();
      } finally {
        vi.unstubAllGlobals();
      }
    },
  );

  it("re-enables a failed alias-only retry without resubmitting polish", async () => {
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    const waitForCompletion = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion")
      .mockResolvedValue({
        ...transformResult(),
        shortLinkAlias: null,
        shortLinkStatus: "unavailable",
      });
    const ensure = vi
      .spyOn(wikiActions, "ensureDocumentAliasAction")
      .mockResolvedValueOnce({ error: "Unavailable", ok: false, status: 503 })
      .mockResolvedValueOnce({
        data: { alias: "architecture-review", fileId: "output-1" },
        ok: true,
      });
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => expect(findButtonNow("Retry short link")).toBeEnabled());
    clickButton(findButtonNow("Retry short link"));
    await vi.waitFor(() => expect(findButtonNow("Retry short link")).toBeEnabled());
    clickButton(findButtonNow("Retry short link"));
    await vi.waitFor(() => expect(document.body).toHaveTextContent("Copy short link"));

    expect(ensure).toHaveBeenCalledTimes(2);
    expect(create).toHaveBeenCalledOnce();
    expect(waitForCompletion).toHaveBeenCalledOnce();
  });

  it("refreshes Drive metadata before creating a transform", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    const refreshExpectedModifiedTime = vi
      .fn<() => Promise<string>>()
      .mockResolvedValue("2026-09-04T13:45:00Z");
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
    const completion = deferred<DocumentTransformResult>();
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
      refreshExpectedModifiedTime,
    });
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => {
      expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledWith(
        expect.objectContaining({ expectedModifiedTime: "2026-09-04T13:45:00Z" }),
        "both",
        expect.any(String),
        "copy",
      );
    });
    expect(refreshExpectedModifiedTime).toHaveBeenCalledOnce();
  });

  it("fails closed when the latest Drive revision cannot be verified", async () => {
    const refreshExpectedModifiedTime = vi
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("Couldn’t verify the latest revision"));
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
      refreshExpectedModifiedTime,
    });
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => {
      expect(refreshExpectedModifiedTime).toHaveBeenCalledOnce();
    });
    expect(create).not.toHaveBeenCalled();
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    expect(document.querySelector('[role="alert"]')).toHaveTextContent(
      "Couldn’t verify the latest revision",
    );
    expect(findButtonNow("Formatting")).toBeEnabled();
    clickButton(findButtonNow("Formatting"));
    expect(document.body).not.toHaveTextContent("Polish interrupted");
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => {
      expect(refreshExpectedModifiedTime).toHaveBeenCalledTimes(2);
      expect(document.body).toHaveTextContent("Polish interrupted");
    });
    clickButton(findButtonNow("Cancel"));
    expect(document.body).not.toHaveTextContent("Polish interrupted");
  });

  it("reuses the refreshed revision and key after an ambiguous create failure", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    const refreshExpectedModifiedTime = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce("2026-09-04T13:45:00Z")
      .mockResolvedValueOnce("2026-09-04T13:46:00Z");
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValueOnce(new Error("Response was lost"))
      .mockResolvedValueOnce(pending);
    const completion = deferred<DocumentTransformResult>();
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
      refreshExpectedModifiedTime,
    });
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => {
      expect(create).toHaveBeenCalledOnce();
      expect(findButtonNow("Resume checking")).toBeEnabled();
      expect(findButtonNow("Formatting")).toBeDisabled();
      expect(document.querySelector('[role="alert"]')).toHaveTextContent("Response was lost");
      expect(document.body).toHaveTextContent("Your existing operation is still reserved");
    });
    expect(document.body).toHaveTextContent("A polish operation is already reserved");
    expect(document.body).not.toHaveTextContent("Checking its status automatically");
    clickButton(findButtonNow("Resume checking"));
    expect(document.body).not.toHaveTextContent("Polish interrupted");

    await vi.waitFor(() => {
      expect(create).toHaveBeenCalledTimes(2);
    });
    expect(refreshExpectedModifiedTime).toHaveBeenCalledOnce();
    expect(create.mock.calls[1]?.[0]).toEqual(create.mock.calls[0]?.[0]);
    expect(create.mock.calls[1]?.[2]).toBe(create.mock.calls[0]?.[2]);
  });

  it("recovers a known task after remount without creating another operation", async () => {
    const source = {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    } as const;
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    writeTransformRecovery("owner-1", "drive:source-1", {
      createdAt: Date.now(),
      key: "66666666-6666-4666-8666-666666666666",
      mode: "format",
      outputStrategy: "copy",
      source: {
        kind: "drive_file",
        fileId: source.fileId,
        name: source.name,
        expectedModifiedTime: "revision",
      },
      taskId: pending.id,
    });
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    vi.spyOn(DOCUMENT_TRANSFORM_API, "getTask").mockResolvedValue(pending);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockRejectedValue(
      new Error("Network unavailable"),
    );
    renderDialog(root, source, undefined, "owner-1");
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(document.body).toHaveTextContent("A polish operation is already reserved");
    expect(document.body).not.toHaveTextContent("Polishing this document");
    clickButton(findButtonNow("Resume checking"));
    await vi.waitFor(() =>
      expect(DOCUMENT_TRANSFORM_API.getTask).toHaveBeenCalledWith(
        pending.id,
        expect.any(AbortSignal),
      ),
    );
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(create).not.toHaveBeenCalled();
    expect(findButtonNow("Formatting")).toBeDisabled();
    expect(document.body).toHaveTextContent(pending.id);
  });

  it.each([
    {
      expectedToast: "Resumed the existing polish",
      task: { id: "55555555-5555-4555-8555-555555555555", status: "running" } as const,
    },
    {
      expectedToast: "Already polished — showing the existing result",
      task: {
        id: "55555555-5555-4555-8555-555555555555",
        result: transformResult(),
        status: "succeeded",
      } as const,
    },
  ])("describes recovered $task.status task truthfully", async ({ expectedToast, task }) => {
    const source = {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    } as const;
    writeTransformRecovery("owner-1", "drive:source-1", {
      createdAt: Date.now(),
      key: "66666666-6666-4666-8666-666666666666",
      mode: "format",
      outputStrategy: "copy",
      source: {
        expectedModifiedTime: source.expectedModifiedTime,
        fileId: source.fileId,
        kind: "drive_file",
        name: source.name,
      },
      taskId: task.id,
    });
    const success = vi.spyOn(toast, "success");
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    vi.spyOn(DOCUMENT_TRANSFORM_API, "getTask").mockResolvedValue(task);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockResolvedValue(transformResult());
    renderDialog(root, source, undefined, "owner-1");
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());

    clickButton(findButtonNow("Resume checking"));

    await vi.waitFor(() => expect(success).toHaveBeenCalledWith(expectedToast));
    expect(success).not.toHaveBeenCalledWith(`Created “${transformResult().outputFile.name}”`);
    expect(create).not.toHaveBeenCalled();
  });

  it("does not create a task when unmounted during the metadata refresh", async () => {
    const refresh = deferred<string>();
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
      refreshExpectedModifiedTime: vi.fn().mockReturnValue(refresh.promise),
    });
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
    clickButton(findButtonNow("Create polished copy"));
    flushSync(() => {
      root.render(null);
    });
    refresh.resolve("2026-09-04T13:45:00Z");
    await Promise.resolve();
    await Promise.resolve();

    expect(create).not.toHaveBeenCalled();
  });

  it("admits only once when the polish button is clicked rapidly", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    const completion = deferred<DocumentTransformResult>();
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    const button = findButtonNow("Create polished copy");

    flushSync(() => {
      button.click();
      button.click();
    });

    await vi.waitFor(() => expect(create).toHaveBeenCalledOnce());
    completion.resolve(transformResult());
    await vi.waitFor(() => expect(document.body).toHaveTextContent("Polish complete"));
  });

  it("describes a retained terminal replay without claiming it created another copy", async () => {
    const result = transformResult();
    const success = vi.spyOn(toast, "success");
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: result.operationId,
      replayed: true,
      result,
      status: "succeeded",
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockResolvedValue(result);
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());

    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() =>
      expect(success).toHaveBeenCalledWith("Already polished — showing the existing result"),
    );
    expect(success).not.toHaveBeenCalledWith(`Created “${result.outputFile.name}”`);
    expect(document.body).toHaveTextContent("Polish complete");
  });

  it("describes an active replay as a resumed polish", async () => {
    const result = transformResult();
    const success = vi.spyOn(toast, "success");
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: result.operationId,
      replayed: true,
      status: "running",
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockResolvedValue(result);
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());

    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => expect(success).toHaveBeenCalledWith("Resumed the existing polish"));
    expect(success).not.toHaveBeenCalledWith(`Created “${result.outputFile.name}”`);
  });

  it("shows actionable reload guidance when the source revision changed", async () => {
    const message =
      "The document changed since polishing was requested. Reload it before trying again.";
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValue(new ApiError(409, message, "document_transform_source_changed"));
    const refreshExpectedModifiedTime = vi.fn().mockResolvedValue("latest-revision");
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
      refreshExpectedModifiedTime,
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());

    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => expect(document.body).toHaveTextContent(message));
    expect(findButtonNow("Create polished copy")).toBeEnabled();
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(refreshExpectedModifiedTime).toHaveBeenCalledTimes(2);
  });

  it("keeps a fresh recovery reserved for an unrelated conflict", async () => {
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValue(new ApiError(409, "Document transform request could not be completed"));
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());

    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(document.body).toHaveTextContent("Your existing operation is still reserved");
    expect(create).toHaveBeenCalledOnce();
  });

  it("names save-result toggles independently of their icons and descriptions", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    });
    await vi.waitFor(() => {
      expect(findButtonNow("Create review copy")).toHaveAccessibleName("Create review copy");
    });
    const copy = findButtonNow("Create review copy");
    const original = findButtonNow("Edit original");
    expect(original).toHaveAccessibleName("Edit original");
    expect(copy).toHaveAccessibleDescription(
      "Keep the original untouched and create a sibling Google Doc.",
    );
    expect(original).toHaveAccessibleDescription(
      "Amend this Google Doc; Drive version history remains available.",
    );
    expect(copy).toHaveAttribute("aria-pressed", "true");
    expect(original).toHaveAttribute("aria-pressed", "false");
    clickButton(original);
    expect(original).toHaveAccessibleName("Edit original");
    expect(original).toHaveAttribute("aria-pressed", "true");
    expect(copy).toHaveAttribute("aria-pressed", "false");
    clickButton(copy);
    expect(copy).toHaveAccessibleName("Create review copy");
    expect(copy).toHaveAttribute("aria-pressed", "true");
    expect(original).toHaveAttribute("aria-pressed", "false");
    expect(create).not.toHaveBeenCalled();
  });

  it("blocks in-place submission while the current revision check is pending", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    const current = deferred<DocumentTransformResult | null>();
    vi.mocked(DOCUMENT_TRANSFORM_API.current).mockReturnValue(current.promise);
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    });

    await vi.waitFor(() => expect(findButtonNow("Edit original")).toBeEnabled());
    clickButton(findButtonNow("Edit original"));

    await vi.waitFor(() => expect(findButtonNow("Checking polish status…")).toBeDisabled());
    clickButton(findButtonNow("Checking polish status…"));
    expect(create).not.toHaveBeenCalled();
    current.resolve(null);
  });

  it("shows the retained receipt instead of re-polishing an unchanged revision", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    const result = transformResult({
      outputFileId: "source-1",
      outputRevisionId: "revision-current",
      outputStrategy: "in_place",
    });
    vi.mocked(DOCUMENT_TRANSFORM_API.current).mockResolvedValue(result);
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    });

    clickButton(findButtonNow("Unslop"));
    await vi.waitFor(() => expect(findButtonNow("Edit original")).toBeEnabled());
    clickButton(findButtonNow("Edit original"));

    await vi.waitFor(() => expect(document.body).toHaveTextContent("Already polished"));
    expect(document.body).toHaveTextContent(result.operationId);
    expect(findButtonNow("Already polished")).toBeDisabled();
    clickButton(findButtonNow("Already polished"));
    expect(create).not.toHaveBeenCalled();

    clickButton(findButtonNow("Create review copy"));
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    expect(document.body).not.toHaveTextContent("This exact document revision already completed");
  });

  it("re-enables in-place polish when the selected mode has no exact receipt", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    const prior = transformResult({
      outputFileId: "source-1",
      outputRevisionId: "revision-current",
      outputStrategy: "in_place",
    });
    vi.mocked(DOCUMENT_TRANSFORM_API.current).mockImplementation(async (_fileId, mode) => {
      await new Promise<void>((resolve) => {
        queueMicrotask(resolve);
      });
      return mode === "unslop" ? prior : null;
    });
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    });

    clickButton(findButtonNow("Unslop"));
    await vi.waitFor(() => expect(findButtonNow("Edit original")).toBeEnabled());
    clickButton(findButtonNow("Edit original"));
    await vi.waitFor(() => expect(findButtonNow("Already polished")).toBeDisabled());

    clickButton(findButtonNow("Formatting"));

    await vi.waitFor(() => expect(findButtonNow("Polish original")).toBeEnabled());
    expect(document.body).not.toHaveTextContent("This exact document revision already completed");
  });

  it("rechecks after returning from Docs and re-enables after a user edit", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    vi.mocked(DOCUMENT_TRANSFORM_API.current)
      .mockResolvedValueOnce(
        transformResult({
          outputFileId: "source-1",
          outputRevisionId: "revision-current",
          outputStrategy: "in_place",
        }),
      )
      .mockResolvedValue(null);
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    });

    clickButton(findButtonNow("Unslop"));
    await vi.waitFor(() => expect(findButtonNow("Edit original")).toBeEnabled());
    clickButton(findButtonNow("Edit original"));
    await vi.waitFor(() => expect(findButtonNow("Already polished")).toBeDisabled());

    window.dispatchEvent(new Event("focus"));

    await vi.waitFor(() => expect(findButtonNow("Polish original")).toBeEnabled());
    expect(DOCUMENT_TRANSFORM_API.current).toHaveBeenCalledTimes(2);
  });

  it.each([true, false])("reports in-place changed=%s accurately", async (changed) => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    const completion =
      Promise.withResolvers<Awaited<ReturnType<typeof DOCUMENT_TRANSFORM_API.waitForCompletion>>>();
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);
    const onInPlaceComplete = vi.fn();

    const { queryClient } = renderDialog(
      root,
      {
        allowInPlace: true,
        expectedModifiedTime: "2026-09-03T12:00:00Z",
        fileId: "source-1",
        kind: "drive_file",
        mimeType: MIME_DOC,
        name: "Architecture review",
      },
      onInPlaceComplete,
    );
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await vi.waitFor(() => {
      expect(findButtonNow("Create review copy")).toHaveAttribute("aria-pressed", "true");
    });
    clickButton(findButtonNow("Edit original"));
    expect(document.body).toHaveTextContent(
      "will be amended only after revision and safety checks",
    );
    await vi.waitFor(() => expect(findButtonNow("Polish original")).toBeEnabled());
    clickButton(findButtonNow("Polish original"));

    await vi.waitFor(() => {
      expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledWith(
        {
          expectedModifiedTime: "2026-09-03T12:00:00Z",
          fileId: "source-1",
          kind: "drive_file",
          name: "Architecture review",
        },
        "both",
        expect.any(String),
        "in_place",
      );
    });
    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Updating and checking the original document…");
    });
    expect(document.body).not.toHaveTextContent("Keep this window open");
    completion.resolve({
      ...transformResult({
        outputFileId: "source-1",
        outputFileName: "Architecture review",
        outputRevisionId: "revision-2",
        outputStrategy: "in_place",
        sourceRevisionId: "revision-1",
      }),
      appliedPatchCount: changed ? 1 : 0,
      changed,
    });

    await vi.waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["wiki", "file", "source-1"] });
      expect(onInPlaceComplete).toHaveBeenCalledOnce();
      expect(document.body).toHaveTextContent(changed ? "Polish complete" : "No changes needed");
    });
    expect(document.body).toHaveTextContent(
      changed ? "1 writing edit applied." : "0 writing edits applied.",
    );
    expect(document.body).toHaveTextContent(
      changed
        ? "Updated the original; Drive version history is available."
        : "Your original is unchanged; no edits were needed.",
    );
    expect(document.body).not.toHaveTextContent(
      changed ? "no edits were needed" : "Updated the original",
    );
    clickButton(findButtonNow("Open polished document"));
    expect(NavigationMock.push).toHaveBeenCalledWith("/d/source-1");
  });

  it("does not submit until in-place capabilities resolve", async () => {
    const capabilityResult =
      Promise.withResolvers<Awaited<ReturnType<typeof DOCUMENT_TRANSFORM_API.capabilities>>>();
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockReturnValue(capabilityResult.promise);
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    expect(document.body).toHaveTextContent("Checking whether this document can be safely edited");
    const pendingButton = findButtonNow("Checking edit options…");
    expect(pendingButton).toBeDisabled();
    clickButton(pendingButton);
    expect(create).not.toHaveBeenCalled();

    capabilityResult.resolve({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    await vi.waitFor(() => {
      expect(findButtonNow("Create polished copy")).toBeEnabled();
      expect(document.body).toHaveTextContent("Edit original");
    });
  });

  it("fails closed and offers retry when edit capabilities cannot be checked", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockRejectedValue(new Error("offline"));
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    const reload = vi.spyOn(window.location, "reload").mockImplementation(vi.fn<() => void>());

    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("No polish will start until you retry");
      expect(findButtonNow("Create polished copy")).toBeDisabled();
      expect(findButtonNow("Retry edit options")).toBeEnabled();
    });
    clickButton(findButtonNow("Create polished copy"));
    expect(create).not.toHaveBeenCalled();
    clickButton(findButtonNow("Reload page"));
    expect(reload).toHaveBeenCalledOnce();
    expect(create).not.toHaveBeenCalled();
  });

  it("does not reload over an operation reserved by another tab", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockRejectedValue(new Error("stale action"));
    const reload = vi.spyOn(window.location, "reload").mockImplementation(vi.fn<() => void>());
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Notes",
    });
    await vi.waitFor(() => expect(findButtonNow("Reload page")).toBeEnabled());
    writeTransformRecovery("owner-1", "drive:source-1", {
      createdAt: Date.now(),
      key: "66666666-6666-4666-8666-666666666666",
      mode: "format",
      outputStrategy: "copy",
      source: null,
      taskId: "55555555-5555-4555-8555-555555555555",
    });
    clickButton(findButtonNow("Reload page"));
    expect(reload).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(document.body).not.toHaveTextContent("Reload page");
    expect(document.body).toHaveTextContent("55555555-5555-4555-8555-555555555555");
  });

  it.each([
    { error: new ApiError(422, "Invalid input"), released: true },
    { error: new ApiError(503, "Unavailable"), released: false },
    {
      error: new ApiError(503, "Unavailable", "document_transform_source_changed"),
      released: false,
    },
    { error: new Error("Network interruption"), released: false },
  ])(
    "only releases a definitive fresh rejection: $released $error.message",
    async ({ error, released }) => {
      const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockRejectedValue(error);
      renderDialog(root, {
        allowInPlace: false,
        expectedModifiedTime: "2026-09-03T12:00:00Z",
        fileId: "source-1",
        kind: "drive_file",
        mimeType: MIME_DOC,
        name: "Architecture review",
      });
      await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
      clickButton(findButtonNow("Create polished copy"));
      await vi.waitFor(() => {
        expect(create).toHaveBeenCalledOnce();
        expect(findButtonNow(released ? "Create polished copy" : "Resume checking")).toBeEnabled();
        expect(findButtonNow("Formatting").disabled).toBe(!released);
      });
    },
  );

  it("blocks accidental resubmission after a terminal task failure", async () => {
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockRejectedValue(
      new DocumentTransformTerminalError("Safety verification failed", {
        code: "transform_failed",
        unverifiedOutputId: "incomplete-copy",
      }),
    );
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Polish did not complete");
      expect(document.querySelector('[role="alert"]')).toHaveTextContent(
        "Task reference: 55555555-5555-4555-8555-555555555555",
      );
      const receipt = document.querySelector('[role="alert"]');
      expect(receipt?.parentElement?.firstElementChild).toBe(receipt);
      expect(receipt?.compareDocumentPosition(findButtonNow("Formatting")) ?? 0).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
      expect(document.body).toHaveTextContent("has not passed safety verification");
      expect(document.querySelector('a[href="/d/incomplete-copy"]')).toHaveTextContent(
        "Open incomplete copy for review",
      );
      expect(findButtonNow("Create polished copy")).toBeDisabled();
    });
    clickButton(findButtonNow("Create polished copy"));
    expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledOnce();
    clickButton(findButtonNow("Formatting"));
    await vi.waitFor(() => {
      expect(document.body).not.toHaveTextContent("Task reference:");
      expect(findButtonNow("Create polished copy")).toBeEnabled();
    });
  });

  it("warns that the original may have changed after post-mutation verification fails", async () => {
    vi.mocked(DOCUMENT_TRANSFORM_API.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockRejectedValue(
      new DocumentTransformTerminalError(
        "Google Docs accepted changes, but safety verification failed.",
        { code: "in_place_verification_failed", unverifiedOutputId: "original-document" },
      ),
    );
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "original-document",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    await vi.waitFor(() => expect(findButtonNow("Edit original")).toBeEnabled());
    clickButton(findButtonNow("Edit original"));
    await vi.waitFor(() => expect(findButtonNow("Polish original")).toBeEnabled());
    clickButton(findButtonNow("Polish original"));

    await vi.waitFor(() => {
      expect(document.querySelector('[role="alert"]')).toHaveTextContent(
        "The original document may have changed",
      );
      expect(document.querySelector('a[href="/d/original-document"]')).toHaveTextContent(
        "Open original document for review",
      );
    });
  });

  it.each([false, true])(
    "enriches terminal create replay and retains its task when read fails=%s",
    async (readFails) => {
      const failed = {
        id: "55555555-5555-4555-8555-555555555555",
        status: "failed",
        errorCode: "output_verification_failed",
        errorDetail: "Task could not be completed",
      } as const;
      const enriched = { ...failed, unverifiedOutputId: "recovered-incomplete-copy" };
      const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(failed);
      const getTask = vi.spyOn(DOCUMENT_TRANSFORM_API, "getTask").mockResolvedValue(enriched);
      if (readFails) getTask.mockRejectedValueOnce(new ApiError(422, "Task read rejected"));
      const wait = vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockRejectedValue(
        new DocumentTransformTerminalError("Safety verification failed", {
          code: failed.errorCode,
          unverifiedOutputId: enriched.unverifiedOutputId,
        }),
      );
      const resume = async () => {
        await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
        expect(wait).not.toHaveBeenCalled();
        clickButton(findButtonNow("Resume checking"));
      };
      renderDialog(root, {
        allowInPlace: false,
        expectedModifiedTime: "2026-09-03T12:00:00Z",
        fileId: "source-1",
        kind: "drive_file",
        mimeType: MIME_DOC,
        name: "Architecture review",
      });
      await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
      clickButton(findButtonNow("Create polished copy"));
      if (readFails) await resume();
      await vi.waitFor(() => {
        expect(document.querySelector('a[href="/d/recovered-incomplete-copy"]')).toHaveTextContent(
          "Open incomplete copy for review",
        );
        expect(document.querySelector('[role="alert"]')).toHaveTextContent(
          `Task reference: ${failed.id}`,
        );
      });
      expect(wait).toHaveBeenCalledWith(enriched, expect.any(AbortSignal));
      expect(getTask).toHaveBeenCalledWith(failed.id, expect.any(AbortSignal));
      expect(create).toHaveBeenCalledOnce();
    },
  );

  it("blocks accidental resubmission after a needs-review outcome", async () => {
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockRejectedValue(
      new DocumentTransformNeedsReviewError(
        "Sarj stopped at a safety check. Review the document and its Drive version history before trying again.",
      ),
    );
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });

    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent(
        "Review the document and its Drive version history before trying again.",
      );
      const receipt = document.querySelector('[role="alert"]');
      expect(receipt?.parentElement?.firstElementChild).toBe(receipt);
      expect(receipt?.compareDocumentPosition(findButtonNow("Formatting")) ?? 0).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
      expect(findButtonNow("Create polished copy")).toBeDisabled();
    });
    clickButton(findButtonNow("Create polished copy"));
    expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledOnce();
  });

  it.each([
    ["ready", "copy"],
    ["unavailable", "copy"],
    [undefined, "copy"],
    ["ready", "in_place"],
  ] as const)(
    "shows persisted review reasons and %s links for %s",
    async (linkStatus, strategy) => {
      const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
      const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
      const result: DocumentTransformResult = {
        ...transformResult({ outputStrategy: strategy }),
        needsReview: true,
        shortLinkStatus: linkStatus ?? null,
        shortLinkAlias: linkStatus === "ready" ? "review-copy" : null,
        findings: [
          {
            blockId: "private-block-id",
            code: "native_date_provider_rejected",
            detail: "Dates were preserved as text.",
          },
          { blockId: null, code: "internal-code-2", detail: "Review the original header." },
          { blockId: null, code: "internal-code-3", detail: "Check document spacing." },
          {
            blockId: null,
            code: "internal-code-4",
            detail: "<script>literal review note</script>",
          },
        ],
      };
      vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockRejectedValue(
        new DocumentTransformNeedsReviewError("Automated safety checks require review.", {
          result,
        }),
      );
      const copy = vi.fn().mockResolvedValue(undefined);
      vi.stubGlobal("navigator", { clipboard: { writeText: copy }, locks: navigator.locks });
      const onInPlaceComplete = vi.fn();
      try {
        renderDialog(
          root,
          {
            allowInPlace: false,
            expectedModifiedTime: "before",
            fileId: "source-1",
            kind: "drive_file",
            mimeType: MIME_DOC,
            name: "Architecture review",
          },
          onInPlaceComplete,
        );
        await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
        clickButton(findButtonNow("Create polished copy"));
        await vi.waitFor(() =>
          expect(document.body).toHaveTextContent("Dates were preserved as text."),
        );
        expect(document.body).toHaveTextContent(`Task reference: ${pending.id}`);
        expect(document.body).not.toHaveTextContent("Polish complete");
        expect(document.body).not.toHaveTextContent("private-block-id");
        expect(document.body).not.toHaveTextContent("internal-code");
        expect(document.querySelector("script")).toBeNull();
        expect(document.body).toHaveTextContent("do not change who can access");
        const more = document.querySelector("details");
        expect(more).not.toHaveAttribute("open");
        expect(more?.querySelector("summary")).toHaveTextContent("Show 1 more review note");
        flushSync(() => more?.querySelector("summary")?.click());
        expect(more).toHaveAttribute("open");
        expect(more).toHaveTextContent("<script>literal review note</script>");
        if (linkStatus === "ready") {
          clickButton(findButtonNow("Copy short link"));
        }
        expect(document.querySelector('a[href="/go/review-copy"]')?.textContent).toBe(
          linkStatus === "ready" ? "Open short link" : undefined,
        );
        expect(copy.mock.calls).toEqual(
          linkStatus === "ready" ? [[`${window.location.origin}/go/review-copy`]] : [],
        );
        expect(document.body.textContent.includes("Copy short link")).toBe(linkStatus === "ready");
        expect(document.body.textContent.includes("Retry short link")).toBe(
          linkStatus === "unavailable",
        );
        expect(document.body.textContent.includes("choose Short link")).toBe(
          linkStatus === undefined,
        );
        expect(findButtonNow("Create polished copy")).toBeDisabled();
        expect(create).toHaveBeenCalledOnce();
        expect(onInPlaceComplete).toHaveBeenCalledTimes(strategy === "in_place" ? 1 : 0);
        clickButton(
          findButtonNow(strategy === "in_place" ? "Open original document" : "Open review copy"),
        );
        expect(NavigationMock.push).toHaveBeenCalledWith("/d/output-1");
      } finally {
        vi.unstubAllGlobals();
      }
    },
  );

  it.each([true, false])(
    "describes verified scoped in-place preservation (changed=%s)",
    async (changed) => {
      const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
      const result = {
        ...transformResult({ outputStrategy: "in_place" }),
        mode: "format" as const,
        changed,
        needsReview: true,
        findings: [
          {
            blockId: "block-1",
            code: "native_rich_text_preserved",
            detail: "Authored text styles were preserved.",
          },
          {
            blockId: "block-title",
            code: "native_visual_authority_preserved",
            detail:
              "Authored visual hierarchy was preserved. Wording cleanup was skipped for these paragraphs.",
          },
          {
            blockId: "block-2",
            code: "native_font_family_preserved",
            detail: "Original font and text styles were preserved where inheritance was uncertain.",
          },
          {
            blockId: null,
            code: "custom_furniture_preserved",
            detail: "Custom headers were preserved. Full Sarj branding was not applied.",
          },
        ],
      };
      vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
      const waitForCompletion = DOCUMENT_TRANSFORM_API.waitForCompletion;
      vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockImplementation(
        async () => await waitForCompletion({ id: pending.id, status: "succeeded", result }),
      );
      renderDialog(root, {
        allowInPlace: false,
        expectedModifiedTime: "before",
        fileId: "source-1",
        kind: "drive_file",
        mimeType: MIME_DOC,
        name: "Notes",
      });
      await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
      clickButton(findButtonNow("Create polished copy"));
      await vi.waitFor(() =>
        expect(document.body).toHaveTextContent(
          changed
            ? "Polish complete with preserved content"
            : "No changes needed; review notes remain",
        ),
      );
      expect(document.body).toHaveTextContent(
        changed ? "Updated the original document." : "The original document is unchanged.",
      );
      expect(document.body).toHaveTextContent("Full Sarj branding was not applied.");
      expect(document.body).toHaveTextContent("Authored text styles were preserved.");
      expect(document.body).toHaveTextContent("Wording cleanup was skipped for these paragraphs.");
      expect(document.body).toHaveTextContent(
        "Original font and text styles were preserved where inheritance was uncertain.",
      );
      expect(document.body).toHaveTextContent(`Task reference: ${pending.id}`);
      expect(document.body).not.toHaveTextContent("Open review copy");
      clickButton(findButtonNow(changed ? "Open updated document" : "Open original document"));
      expect(NavigationMock.push).toHaveBeenCalledWith("/d/output-1");
      expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledOnce();
    },
  );

  it.each([
    "native_paragraph_layout_preserved",
    "native_font_family_preserved",
    "native_visual_authority_preserved",
    "table_font_scope",
    "table_font_preserved",
  ])("recognizes verified preservation notes without hiding other failures: %s", (code) => {
    const result: DocumentTransformResult = {
      ...transformResult({ outputStrategy: "in_place" }),
      changed: true,
      mode: "format",
      needsReview: true,
      findings: [{ blockId: null, code, detail: "Review preserved formatting." }],
    };
    expect(preservedInPlaceOutcome(result)?.heading).toBe("Polish complete with preserved content");
    expect(result.needsReview).toBe(true);
    result.changed = false;
    expect(preservedInPlaceOutcome(result)?.heading).toBe("No changes needed; review notes remain");
    result.validation.structurePreserved = false;
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.validation.structurePreserved = true;
    result.validation.protectedContentPreserved = false;
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.validation.protectedContentPreserved = true;
    result.validation.unsafePatchCount = 1;
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.validation.unsafePatchCount = 0;
    result.needsReview = false;
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.needsReview = true;
    result.outputStrategy = "copy";
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.outputStrategy = "in_place";
    result.artifactKind = "presentation";
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.artifactKind = "document";
    result.mode = "unslop";
    expect(preservedInPlaceOutcome(result)).toBeNull();
    result.mode = "both";
    expect(preservedInPlaceOutcome(result)?.heading).toBe("No changes needed; review notes remain");
    result.findings.push({
      blockId: null,
      code: "verification_failed",
      detail: "Review required.",
    });
    expect(preservedInPlaceOutcome(result)).toBeNull();
  });

  it.each([
    "empty",
    "unknown",
    "mixed",
    "content",
    "structure",
    "unsafe",
    "copy",
    "no-review",
    "unslop",
  ] as const)(
    "does not describe an unproven or unrelated review as preservation success: %s",
    (reason) => {
      const result: DocumentTransformResult = {
        ...transformResult({ outputStrategy: "in_place" }),
        mode: "format",
        needsReview: true,
        findings: [
          { blockId: null, code: "custom_furniture_preserved", detail: "Preserved custom header." },
        ],
      };
      switch (reason) {
        case "empty":
          result.findings = [];
          break;
        case "unknown":
          result.findings = [
            { blockId: null, code: "verification_failed", detail: "Review required." },
          ];
          break;
        case "mixed":
          result.findings.push({
            blockId: null,
            code: "native_date_provider_rejected",
            detail: "Date fallback.",
          });
          break;
        case "content":
          result.validation.protectedContentPreserved = false;
          break;
        case "structure":
          result.validation.structurePreserved = false;
          break;
        case "unsafe":
          result.validation.unsafePatchCount = 1;
          break;
        case "copy":
          result.outputStrategy = "copy";
          break;
        case "no-review":
          result.needsReview = false;
          break;
        case "unslop":
          result.mode = "unslop";
          break;
      }
      expect(preservedInPlaceOutcome(result)).toBeNull();
    },
  );

  it("never offers in-place for a Drive-hosted Markdown file", async () => {
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(
      Promise.withResolvers<DocumentTransformResult>().promise,
    );

    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "markdown-1",
      kind: "drive_file",
      mimeType: "text/markdown",
      name: "Notes.md",
    });

    await Promise.resolve();
    expect(DOCUMENT_TRANSFORM_API.capabilities).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("Edit original");
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => {
      expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledWith(
        {
          expectedModifiedTime: "2026-09-03T12:00:00Z",
          fileId: "markdown-1",
          kind: "drive_file",
          name: "Notes.md",
        },
        "both",
        expect.any(String),
        "copy",
      );
    });
  });

  it("offers Word formatting only and submits a type-preserving copy", async () => {
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValue(new Error("offline"));
    renderDialog(root, {
      allowInPlace: true,
      expectedModifiedTime: "revision",
      fileId: "word-1",
      kind: "drive_file",
      mimeType: MIME_DOCX,
      name: "Security Policy.docx",
    });
    expect(document.body).toHaveTextContent("new Word document (.docx)");
    expect(document.body).toHaveTextContent("review pagination and fields in Word");
    expect(document.body).not.toHaveTextContent("Edit original");
    expect(document.body).not.toHaveTextContent("Unslop");
    expect(document.body).not.toHaveTextContent("complete Sarj brand system");
    expect(DOCUMENT_TRANSFORM_API.capabilities).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ fileId: "word-1" }),
        "format",
        expect.any(String),
        "copy",
      ),
    );
  });

  it("never rewrites an older Word BOTH recovery into FORMAT", async () => {
    const savedSource = {
      expectedModifiedTime: "revision",
      fileId: "word-1",
      kind: "drive_file",
      name: "Policy.docx",
    } as const;
    const key = "66666666-6666-4666-8666-666666666666";
    writeTransformRecovery("owner-1", "drive:word-1", {
      createdAt: Date.now(),
      key,
      mode: "both",
      outputStrategy: "copy",
      source: savedSource,
      taskId: null,
    });
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValue(new ApiError(422, "Word supports formatting only"));
    renderDialog(root, {
      ...savedSource,
      allowInPlace: false,
      mimeType: MIME_DOCX,
      name: "Policy.docx",
    });
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(findButtonNow("Both")).toHaveAttribute("aria-pressed", "true");
    expect(findButtonNow("Both")).toBeDisabled();
    clickButton(findButtonNow("Resume checking"));
    await vi.waitFor(() => expect(create).toHaveBeenCalledWith(savedSource, "both", key, "copy"));
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(findButtonNow("Both")).toHaveAttribute("aria-pressed", "true");
  });

  it("presents native Slides as a copy-only presentation transform", () => {
    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-04T12:00:00Z",
      fileId: "slides-1",
      kind: "drive_file",
      mimeType: MIME_SLIDE,
      name: "Quarterly review",
    });

    expect(document.body).toHaveTextContent("Polish presentation");
    expect(document.body).toHaveTextContent("new Google Slides presentation");
    expect(document.body).not.toHaveTextContent("Edit original");
    expect(DOCUMENT_TRANSFORM_API.capabilities).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    "restores the exact Markdown draft and opens its result: changed=%s",
    async (changed) => {
      const operation = {
        createdAt: Date.now(),
        key: "77777777-7777-4777-8777-777777777777",
        mode: "unslop",
        outputStrategy: "copy",
        source: null,
        taskId: null,
      } as const;
      const originalSource = {
        kind: "markdown",
        content: "# Exact original\n\n  Private 😀\r\n",
        name: "Original name",
        destinationFolderId: "original-folder",
      } as const;
      writeTransformRecovery("owner-1", "markdown", operation);
      saveMarkdownRecovery(makeMarkdownRecovery("owner-1", operation, originalSource));
      vi.spyOn(DOCUMENT_TRANSFORM_API, "admission").mockResolvedValue(null);
      const create = vi
        .spyOn(DOCUMENT_TRANSFORM_API, "create")
        .mockResolvedValue({ id: "55555555-5555-4555-8555-555555555555", status: "pending" });
      const completion = deferred<DocumentTransformResult>();
      vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(completion.promise);
      renderDialog(root, {
        kind: "markdown",
        initialDestination: {
          driveId: "drive-1",
          driveName: "Current workspace",
          folderId: "different-current-folder",
          folderPath: [],
        },
      });
      await vi.waitFor(() => expect(findButtonNow("Restore saved recovery draft")).toBeEnabled());
      expect(findButtonNow("Resume checking")).toBeDisabled();
      clickButton(findButtonNow("Restore saved recovery draft"));
      await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
      expect(document.querySelector("#polish-markdown-name")).toHaveValue(originalSource.name);
      expect(document.querySelector("#polish-markdown-content")).toHaveValue(
        originalSource.content,
      );
      expect(document.body).toHaveTextContent(
        `${new TextEncoder().encode(originalSource.content).length} /`,
      );
      expect(findButtonNow("Unslop")).toHaveAttribute("aria-pressed", "true");
      expect(findButtonNow("Both")).toHaveAttribute("aria-pressed", "false");
      expect(findButtonNow("Saved destination folder")).toHaveTextContent(
        `Folder ID: ${originalSource.destinationFolderId}`,
      );
      expect(findButtonNow("Saved destination folder")).toBeDisabled();
      expect(document.body).not.toHaveTextContent("Current workspace");
      expect(create).not.toHaveBeenCalled();
      clickButton(findButtonNow("Resume checking"));
      await vi.waitFor(() =>
        expect(create).toHaveBeenCalledExactlyOnceWith(
          originalSource,
          "unslop",
          operation.key,
          "copy",
        ),
      );
      expect(document.querySelector("#polish-markdown-name")).toHaveValue(originalSource.name);
      expect(document.querySelector("#polish-markdown-content")).toHaveValue(
        originalSource.content,
      );
      expect(findButtonNow("Unslop")).toHaveAttribute("aria-pressed", "true");
      completion.resolve({ ...transformResult(), changed });
      await vi.waitFor(() => expect(findButtonNow("Open polished document")).toBeEnabled());
      expect(document.querySelector("#polish-markdown-name")).toBeNull();
      expect(document.querySelector("#polish-markdown-content")).toBeNull();
      expect(findButtonNow("Unslop")).toHaveAttribute("aria-pressed", "true");
      expect(findButtonNow("Both")).toHaveAttribute("aria-pressed", "false");
      expect(NavigationMock.push).not.toHaveBeenCalled();
      clickButton(findButtonNow("Open polished document"));
      expect(NavigationMock.push).toHaveBeenCalledExactlyOnceWith("/d/output-1");
      expect(create).toHaveBeenCalledOnce();
    },
  );

  it("requires a secure owner for Markdown recovery and never auto-submits after identity returns", async () => {
    const source: DialogTestSource = {
      kind: "markdown",
      initialDestination: {
        driveId: "drive-1",
        driveName: "Workspace",
        folderId: "folder-1",
        folderPath: [],
      },
    };
    const create = vi
      .spyOn(DOCUMENT_TRANSFORM_API, "create")
      .mockRejectedValue(new Error("offline"));
    renderDialog(root, source, undefined, null);
    typeTextarea("# Private draft");
    expect(document.body).toHaveTextContent("Secure session identity unavailable");
    expect(findButtonNow("Import Google Doc")).toBeDisabled();
    clickButton(findButtonNow("Import Google Doc"));
    expect(create).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
    renderDialog(root, source, undefined, "owner-1");
    await vi.waitFor(() => expect(findButtonNow("Import Google Doc")).toBeEnabled());
    expect(create).not.toHaveBeenCalled();
    clickButton(findButtonNow("Import Google Doc"));
    await vi.waitFor(() => expect(create).toHaveBeenCalledOnce());
  });

  it("requires pasted Markdown and sends the current folder destination", async () => {
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    const pendingCompletion =
      Promise.withResolvers<Awaited<ReturnType<typeof DOCUMENT_TRANSFORM_API.waitForCompletion>>>();
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockReturnValue(
      pendingCompletion.promise,
    );
    renderDialog(root, {
      initialDestination: {
        driveId: "drive-1",
        driveName: "Current workspace",
        folderId: "folder-1",
        folderPath: ["Current folder"],
      },
      kind: "markdown",
    });

    expect(findButtonNow("Import Google Doc")).toBeDisabled();
    typeTextarea("# Launch\n\nA concise plan.");
    await vi.waitFor(() => expect(findButtonNow("Import Google Doc")).toBeEnabled());
    clickButton(findButtonNow("Formatting"));
    clickButton(findButtonNow("Import Google Doc"));

    await vi.waitFor(() => {
      expect(DOCUMENT_TRANSFORM_API.create).toHaveBeenCalledWith(
        {
          content: "# Launch\n\nA concise plan.",
          destinationFolderId: "folder-1",
          kind: "markdown",
          name: "Untitled document",
        },
        "format",
        expect.any(String),
        "copy",
      );
    });
    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Importing and checking the new document…");
    });
    expect(document.body).not.toHaveTextContent("Keep this window open");
  });

  it("loads dropped UTF-8 Markdown and derives the document name", async () => {
    renderDialog(root, {
      initialDestination: {
        driveId: "drive-1",
        driveName: "Current workspace",
        folderId: "folder-1",
        folderPath: ["Current folder"],
      },
      kind: "markdown",
    });
    const dropZone = document.body.querySelector<HTMLElement>(
      '[aria-label="Markdown file drop zone"]',
    );
    if (dropZone === null) throw new Error("Markdown drop zone not found");
    const file = new File(["# Imported\n\nBody."], "Architecture.md", {
      type: "text/markdown",
    });
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", {
      value: { files: { item: () => file } },
    });

    dropZone.dispatchEvent(drop);

    await vi.waitFor(() => {
      expect(document.body.querySelector<HTMLInputElement>("#polish-markdown-name")).toHaveValue(
        "Architecture",
      );
      expect(document.body.querySelector("textarea")).toHaveValue("# Imported\n\nBody.");
      expect(findButtonNow("Import Google Doc")).toBeEnabled();
    });
  });

  it("cannot submit an old draft while a selected file is still loading", async () => {
    renderDialog(root, {
      initialDestination: {
        driveId: "drive-1",
        driveName: "Workspace",
        folderId: "folder-1",
        folderPath: [],
      },
      kind: "markdown",
    });
    typeTextarea("# Old draft");
    await vi.waitFor(() => expect(findButtonNow("Import Google Doc")).toBeEnabled());
    const bytes = deferred<ArrayBuffer>();
    const file = new File(["# New draft"], "New.md");
    vi.spyOn(file, "arrayBuffer").mockReturnValue(bytes.promise);
    const dropZone = document.body.querySelector<HTMLElement>(
      '[aria-label="Markdown file drop zone"]',
    );
    if (!dropZone) throw new Error("Markdown drop zone not found");
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", {
      value: { files: { item: () => file } },
    });
    flushSync(() => dropZone.dispatchEvent(drop));

    expect(document.body).toHaveTextContent("Reading Markdown file…");
    expect(findButtonNow("Import Google Doc")).toBeDisabled();
    const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
    clickButton(findButtonNow("Import Google Doc"));
    expect(create).not.toHaveBeenCalled();

    bytes.resolve(new TextEncoder().encode("# New draft").buffer);
    await vi.waitFor(() => {
      expect(document.body.querySelector("textarea")).toHaveValue("# New draft");
      expect(findButtonNow("Import Google Doc")).toBeEnabled();
    });
  });

  it("keeps manual edits made after a slow file selection", async () => {
    renderDialog(root, {
      initialDestination: {
        driveId: "drive-1",
        driveName: "Workspace",
        folderId: "folder-1",
        folderPath: [],
      },
      kind: "markdown",
    });
    const bytes = deferred<ArrayBuffer>();
    const file = new File(["# File draft"], "File.md");
    vi.spyOn(file, "arrayBuffer").mockReturnValue(bytes.promise);
    const dropZone = document.body.querySelector<HTMLElement>(
      '[aria-label="Markdown file drop zone"]',
    );
    if (!dropZone) throw new Error("Markdown drop zone not found");
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", {
      value: { files: { item: () => file } },
    });
    flushSync(() => dropZone.dispatchEvent(drop));
    typeTextarea("# Manual draft");
    expect(document.body).not.toHaveTextContent("Reading Markdown file…");

    bytes.resolve(new TextEncoder().encode("# File draft").buffer);
    await vi.waitFor(() => {
      expect(document.body.querySelector("textarea")).toHaveValue("# Manual draft");
      expect(document.body.querySelector<HTMLInputElement>("#polish-markdown-name")).toHaveValue(
        "Untitled document",
      );
    });
  });

  it("reports unsupported tab naming as a preserved optional enhancement", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    vi.spyOn(DOCUMENT_TRANSFORM_API, "create").mockResolvedValue(pending);
    vi.spyOn(DOCUMENT_TRANSFORM_API, "waitForCompletion").mockResolvedValue({
      ...transformResult(),
      findings: [
        {
          blockId: null,
          code: "tab_naming_preserved",
          detail:
            "Tab names and icons were preserved because this document's tab metadata could not be safely renamed.",
          severity: "informational",
        },
      ],
    });

    renderDialog(root, {
      allowInPlace: false,
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
      mimeType: MIME_DOC,
      name: "Architecture review",
    });
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));

    await vi.waitFor(() => expect(document.body).toHaveTextContent("Polish complete"));
    expect(document.body).toHaveTextContent("Tab names and icons were preserved");
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(document.body).not.toHaveTextContent("This document needs review");
  });
});

type DialogTestSource =
  | DocumentPolishSource
  | (Omit<Extract<DocumentPolishSource, { kind: "drive_file" }>, "refreshExpectedModifiedTime"> & {
      refreshExpectedModifiedTime?: () => Promise<string>;
    });

function renderDialog(
  root: Root,
  source: DialogTestSource,
  onInPlaceComplete?: () => void,
  owner: null | string = "owner-1",
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let normalizedSource: DocumentPolishSource;
  if (source.kind === "drive_file") {
    normalizedSource = {
      ...source,
      refreshExpectedModifiedTime:
        source.refreshExpectedModifiedTime ??
        (async () => {
          await new Promise<void>((resolve) => {
            queueMicrotask(resolve);
          });
          return source.expectedModifiedTime;
        }),
    };
  } else {
    normalizedSource = source;
  }
  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <BrowserUserContext value={owner}>
          <DocumentPolishDialog
            onOpenChange={vi.fn()}
            open
            source={normalizedSource}
            {...(onInPlaceComplete === undefined ? {} : { onInPlaceComplete })}
          />
        </BrowserUserContext>
      </QueryClientProvider>,
    );
  });
  return { queryClient };
}

function clickButton(button: HTMLButtonElement) {
  flushSync(() => {
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.click();
  });
}

function findButtonNow(text: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
    (candidate) => candidate.textContent.includes(text),
  );
  if (button === undefined) throw new Error(`Button not found: ${text}`);
  return button;
}

interface TransformResultOverrides {
  outputFileId?: string;
  outputFileName?: string;
  outputRevisionId?: null | string;
  outputStrategy?: "copy" | "in_place";
  sourceRevisionId?: null | string;
}

function transformResult(overrides: TransformResultOverrides = {}): DocumentTransformResult {
  const outputFileId = overrides.outputFileId ?? "output-1";
  const outputFileName = overrides.outputFileName ?? "Architecture review — Unslopped";
  return {
    accessScope: "inherited",
    appliedPatchCount: 1,
    artifactKind: "document",
    brandProfileId: "sarj-2026-v4",
    brandProfileSha256: "b".repeat(64),
    changed: true,
    deterministicPatchCount: 1,
    promotedHeadingCount: 0,
    findings: [],
    llmInvoked: false,
    mode: "unslop",
    modelPatchCount: 0,
    modelIds: [],
    needsReview: false,
    operationId: "55555555-5555-4555-8555-555555555555",
    outputFile: {
      canEdit: true,
      driveId: "drive-1",
      id: outputFileId,
      mimeType: MIME_DOC,
      modifiedTime: "2026-09-03T12:01:00Z",
      name: outputFileName,
      parentFolderId: "folder-1",
    },
    outputRevisionId: overrides.outputRevisionId ?? null,
    outputStrategy: overrides.outputStrategy ?? "copy",
    outputWebUrl: `https://docs.google.com/document/d/${outputFileId}/edit`,
    pipelineVersion: "document-transform-v3",
    sourceRevisionId: overrides.sourceRevisionId ?? null,
    sourceSha256: "a".repeat(64),
    toolVersions: { unslop: "d81f519" },
    validation: {
      protectedContentPreserved: true,
      sourceUnchanged: true,
      structurePreserved: true,
      unsafePatchCount: 0,
    },
  };
}

function typeTextarea(value: string) {
  const textarea = document.body.querySelector("textarea");
  if (textarea === null) throw new Error("Markdown textarea not found");
  // eslint-disable-next-line @typescript-eslint/unbound-method -- The native setter is immediately rebound to the test element.
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  if (setter === undefined) throw new Error("Textarea value setter not found");
  flushSync(() => {
    setter.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function deferred<T>() {
  return Promise.withResolvers<T>();
}
