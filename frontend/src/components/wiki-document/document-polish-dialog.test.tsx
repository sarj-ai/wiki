import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { documentTransformApi } from "@/lib/document-transform-client";
import { writeTransformRecovery } from "@/lib/document-transform-recovery";
import {
  DocumentTransformNeedsReviewError,
  type DocumentTransformResult,
  DocumentTransformTerminalError,
} from "@/lib/document-transform-types";
import { installLocalStorage } from "@/test-stubs/local-storage";
import { MIME_DOC, MIME_SLIDE } from "@/types/wiki";

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
    vi.spyOn(documentTransformApi, "capabilities").mockResolvedValue({
      copy: true,
      inPlace: false,
      presentationCopy: true,
      presentationInPlace: false,
    });
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
    const create = vi.spyOn(documentTransformApi, "create").mockRejectedValue(new Error("offline"));
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
    vi.spyOn(documentTransformApi, "create").mockResolvedValue(pending);
    const completion =
      deferred<Awaited<ReturnType<typeof documentTransformApi.waitForCompletion>>>();
    vi.spyOn(documentTransformApi, "waitForCompletion").mockReturnValue(completion.promise);

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
    expect(documentTransformApi.create).toHaveBeenCalledWith(
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
    expect(documentTransformApi.create).toHaveBeenCalledTimes(1);
    expect(documentTransformApi.waitForCompletion).toHaveBeenCalledWith(
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

  it.each(["ready", "unavailable"] as const)(
    "shows the %s link outcome without repolishing",
    async (shortLinkStatus) => {
      const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
      const create = vi.spyOn(documentTransformApi, "create").mockResolvedValue(pending);
      vi.spyOn(documentTransformApi, "waitForCompletion").mockResolvedValue({
        ...transformResult(),
        changed: false,
        shortLinkAlias: shortLinkStatus === "ready" ? "architecture-review" : null,
        shortLinkStatus,
      });
      const copy = vi.fn().mockResolvedValue(undefined);
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
        if (shortLinkStatus === "ready") {
          clickButton(findButtonNow("Copy short link"));
        }
        expect(copy.mock.calls).toEqual(
          shortLinkStatus === "ready" ? [[`${window.location.origin}/go/architecture-review`]] : [],
        );
        expect(document.body).toHaveTextContent(
          shortLinkStatus === "ready" ? "Copy short link" : "short link could not be created",
        );
        expect(create).toHaveBeenCalledOnce();
        expect(NavigationMock.push).not.toHaveBeenCalled();
      } finally {
        vi.unstubAllGlobals();
      }
    },
  );

  it("refreshes Drive metadata before creating a transform", async () => {
    const pending = { id: "55555555-5555-4555-8555-555555555555", status: "pending" } as const;
    const refreshExpectedModifiedTime = vi
      .fn<() => Promise<string>>()
      .mockResolvedValue("2026-09-04T13:45:00Z");
    vi.spyOn(documentTransformApi, "create").mockResolvedValue(pending);
    const completion = deferred<DocumentTransformResult>();
    vi.spyOn(documentTransformApi, "waitForCompletion").mockReturnValue(completion.promise);

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
      expect(documentTransformApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ expectedModifiedTime: "2026-09-04T13:45:00Z" }),
        "both",
        expect.any(String),
        "copy",
      );
    });
    expect(refreshExpectedModifiedTime).toHaveBeenCalledTimes(1);
  });

  it("fails closed when the latest Drive revision cannot be verified", async () => {
    const refreshExpectedModifiedTime = vi
      .fn<() => Promise<string>>()
      .mockRejectedValue(new Error("Couldn’t verify the latest revision"));
    const create = vi.spyOn(documentTransformApi, "create");

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
      expect(refreshExpectedModifiedTime).toHaveBeenCalledTimes(1);
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
      .spyOn(documentTransformApi, "create")
      .mockRejectedValueOnce(new Error("Response was lost"))
      .mockResolvedValueOnce(pending);
    const completion = deferred<DocumentTransformResult>();
    vi.spyOn(documentTransformApi, "waitForCompletion").mockReturnValue(completion.promise);

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
      expect(create).toHaveBeenCalledTimes(1);
      expect(findButtonNow("Resume checking")).toBeEnabled();
      expect(findButtonNow("Formatting")).toBeDisabled();
      expect(document.querySelector('[role="alert"]')).toHaveTextContent("Response was lost");
      expect(document.body).toHaveTextContent("Your existing operation is still reserved");
    });
    clickButton(findButtonNow("Resume checking"));
    expect(document.body).not.toHaveTextContent("Polish interrupted");

    await vi.waitFor(() => {
      expect(create).toHaveBeenCalledTimes(2);
    });
    expect(refreshExpectedModifiedTime).toHaveBeenCalledTimes(1);
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
    const create = vi.spyOn(documentTransformApi, "create");
    vi.spyOn(documentTransformApi, "getTask").mockResolvedValue(pending);
    vi.spyOn(documentTransformApi, "waitForCompletion").mockRejectedValue(
      new Error("Network unavailable"),
    );
    renderDialog(root, source, undefined, "owner-1");
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    clickButton(findButtonNow("Resume checking"));
    await vi.waitFor(() =>
      expect(documentTransformApi.getTask).toHaveBeenCalledWith(
        pending.id,
        expect.any(AbortSignal),
      ),
    );
    await vi.waitFor(() => expect(findButtonNow("Resume checking")).toBeEnabled());
    expect(create).not.toHaveBeenCalled();
    expect(findButtonNow("Formatting")).toBeDisabled();
    expect(document.body).toHaveTextContent(pending.id);
  });

  it("does not create a task when unmounted during the metadata refresh", async () => {
    const refresh = deferred<string>();
    const create = vi.spyOn(documentTransformApi, "create");

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

  it("offers server-gated in-place editing while defaulting to a review copy", async () => {
    vi.mocked(documentTransformApi.capabilities).mockResolvedValue({
      copy: true,
      inPlace: true,
      presentationCopy: true,
      presentationInPlace: false,
    });
    vi.spyOn(documentTransformApi, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    const completion =
      Promise.withResolvers<Awaited<ReturnType<typeof documentTransformApi.waitForCompletion>>>();
    vi.spyOn(documentTransformApi, "waitForCompletion").mockReturnValue(completion.promise);
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
    clickButton(findButtonNow("Polish original"));

    await vi.waitFor(() => {
      expect(documentTransformApi.create).toHaveBeenCalledWith(
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
    completion.resolve(
      transformResult({
        outputFileId: "source-1",
        outputFileName: "Architecture review",
        outputRevisionId: "revision-2",
        outputStrategy: "in_place",
        sourceRevisionId: "revision-1",
      }),
    );

    await vi.waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["wiki", "file", "source-1"] });
      expect(onInPlaceComplete).toHaveBeenCalledOnce();
      expect(document.body).toHaveTextContent("Polish complete");
    });
    clickButton(findButtonNow("Open polished document"));
    expect(NavigationMock.push).toHaveBeenCalledWith("/d/source-1");
  });

  it("does not submit until in-place capabilities resolve", async () => {
    const capabilityResult =
      Promise.withResolvers<Awaited<ReturnType<typeof documentTransformApi.capabilities>>>();
    vi.mocked(documentTransformApi.capabilities).mockReturnValue(capabilityResult.promise);
    const create = vi.spyOn(documentTransformApi, "create");

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
    vi.mocked(documentTransformApi.capabilities).mockRejectedValue(new Error("offline"));
    const create = vi.spyOn(documentTransformApi, "create");

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
  });

  it.each([
    { error: new ApiError(422, "Invalid input"), released: true },
    { error: new ApiError(503, "Unavailable"), released: false },
    { error: new Error("Network interruption"), released: false },
  ])(
    "only releases a definitive fresh rejection: $released $error.message",
    async ({ error, released }) => {
      const create = vi.spyOn(documentTransformApi, "create").mockRejectedValue(error);
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
        expect(create).toHaveBeenCalledTimes(1);
        expect(findButtonNow(released ? "Create polished copy" : "Resume checking")).toBeEnabled();
        expect(findButtonNow("Formatting").disabled).toBe(!released);
      });
    },
  );

  it("blocks accidental resubmission after a terminal task failure", async () => {
    vi.spyOn(documentTransformApi, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(documentTransformApi, "waitForCompletion").mockRejectedValue(
      new DocumentTransformTerminalError(
        "transform_failed",
        "Safety verification failed",
        "incomplete-copy",
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
    expect(documentTransformApi.create).toHaveBeenCalledTimes(1);
    clickButton(findButtonNow("Formatting"));
    await vi.waitFor(() => {
      expect(document.body).not.toHaveTextContent("Task reference:");
      expect(findButtonNow("Create polished copy")).toBeEnabled();
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
      const create = vi.spyOn(documentTransformApi, "create").mockResolvedValue(failed);
      const getTask = vi.spyOn(documentTransformApi, "getTask").mockResolvedValue(enriched);
      if (readFails) getTask.mockRejectedValueOnce(new ApiError(422, "Task read rejected"));
      const wait = vi
        .spyOn(documentTransformApi, "waitForCompletion")
        .mockRejectedValue(
          new DocumentTransformTerminalError(
            failed.errorCode,
            "Safety verification failed",
            enriched.unverifiedOutputId,
          ),
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
      expect(create).toHaveBeenCalledTimes(1);
    },
  );

  it("blocks accidental resubmission after a needs-review outcome", async () => {
    vi.spyOn(documentTransformApi, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(documentTransformApi, "waitForCompletion").mockRejectedValue(
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
    expect(documentTransformApi.create).toHaveBeenCalledTimes(1);
  });

  it("never offers in-place for a Drive-hosted Markdown file", async () => {
    vi.spyOn(documentTransformApi, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    vi.spyOn(documentTransformApi, "waitForCompletion").mockReturnValue(
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
    expect(documentTransformApi.capabilities).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("Edit original");
    await vi.waitFor(() => expect(findButtonNow("Create polished copy")).toBeEnabled());
    clickButton(findButtonNow("Create polished copy"));
    await vi.waitFor(() => {
      expect(documentTransformApi.create).toHaveBeenCalledWith(
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
    expect(documentTransformApi.capabilities).not.toHaveBeenCalled();
  });

  it("requires pasted Markdown and sends the current folder destination", async () => {
    vi.spyOn(documentTransformApi, "create").mockResolvedValue({
      id: "55555555-5555-4555-8555-555555555555",
      status: "pending",
    });
    const pendingCompletion =
      Promise.withResolvers<Awaited<ReturnType<typeof documentTransformApi.waitForCompletion>>>();
    vi.spyOn(documentTransformApi, "waitForCompletion").mockReturnValue(pendingCompletion.promise);
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
      expect(documentTransformApi.create).toHaveBeenCalledWith(
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
