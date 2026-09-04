import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { documentTransformApi } from "@/lib/document-transform-client";
import { MIME_DOC } from "@/types/wiki";

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
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
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
      expectedModifiedTime: "2026-09-03T12:00:00Z",
      fileId: "source-1",
      kind: "drive_file",
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
    );
    expect(documentTransformApi.waitForCompletion).toHaveBeenCalledWith(
      pending,
      expect.any(AbortSignal),
    );

    completion.resolve({
      appliedPatchCount: 1,
      brandProfileId: "sarj-2026-v2",
      brandProfileSha256: "b".repeat(64),
      deterministicPatchCount: 1,
      findings: [],
      llmInvoked: false,
      mode: "unslop",
      modelPatchCount: 0,
      modelIds: [],
      needsReview: false,
      operationId: pending.id,
      outputFile: {
        canEdit: true,
        driveId: "drive-1",
        id: "output-1",
        mimeType: MIME_DOC,
        modifiedTime: "2026-09-03T12:01:00Z",
        name: "Architecture review — Unslopped",
        parentFolderId: "folder-1",
      },
      outputWebUrl: "https://docs.google.com/document/d/output-1/edit",
      pipelineVersion: "sarj-v1",
      sourceSha256: "a".repeat(64),
      toolVersions: { unslop: "d81f519" },
      validation: {
        protectedContentPreserved: true,
        sourceUnchanged: true,
        structurePreserved: true,
        unsafePatchCount: 0,
      },
    });

    await vi.waitFor(() => {
      expect(NavigationMock.push).toHaveBeenCalledWith("/d/output-1");
    });
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
      );
    });
  });
});

function renderDialog(root: Root, source: DocumentPolishSource) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <DocumentPolishDialog onOpenChange={vi.fn()} open source={source} />
      </QueryClientProvider>,
    );
  });
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
