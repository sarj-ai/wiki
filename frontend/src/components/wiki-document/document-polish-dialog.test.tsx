import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { documentTransformApi } from "@/lib/document-transform-client";
import type { DocumentTransformResult } from "@/lib/document-transform-types";
import { MIME_DOC, MIME_SLIDE } from "@/types/wiki";

import { DocumentPolishDialog, type DocumentPolishSource } from "./document-polish-dialog";

const NavigationMock = vi.hoisted(() => ({ push: vi.fn() }));

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
    clickButton(findButtonNow("Create polished copy"));

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
    expect(documentTransformApi.waitForCompletion).toHaveBeenCalledWith(
      pending,
      expect.any(AbortSignal),
    );

    completion.resolve(transformResult());

    await vi.waitFor(() => {
      expect(NavigationMock.push).toHaveBeenCalledWith("/d/output-1");
    });
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
      expect(NavigationMock.push).toHaveBeenCalledWith("/d/source-1");
    });
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

    expect(findButtonNow("Create polished copy")).toBeDisabled();
    typeTextarea("# Launch\n\nA concise plan.");
    expect(findButtonNow("Create polished copy")).toBeEnabled();
    clickButton(findButtonNow("Formatting"));
    clickButton(findButtonNow("Create polished copy"));

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
});

function renderDialog(root: Root, source: DocumentPolishSource, onInPlaceComplete?: () => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <DocumentPolishDialog
          onOpenChange={vi.fn()}
          open
          source={source}
          {...(onInPlaceComplete === undefined ? {} : { onInPlaceComplete })}
        />
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
    brandProfileId: "sarj-2026-v3",
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
