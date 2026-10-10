import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import { smartCreateApi } from "@/lib/smart-create-client";
import type { DriveTemplate } from "@/lib/smart-create-types";
import { MIME_DOC } from "@/types/wiki";

import { SmartCreateDialog } from "./smart-create-dialog";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const template: DriveTemplate = {
  categoryPath: ["Product", "PRDs"],
  description: "Product requirements",
  driveId: "drive-1",
  driveName: "Product",
  fileId: "template-1",
  mimeType: MIME_DOC,
  modifiedTime: "2026-08-02T12:00:00Z",
  name: "PRD Template",
};

describe("SmartCreateDialog", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    host.remove();
    vi.restoreAllMocks();
  });

  it("uses TemplateMode without invoking Gemini", async () => {
    vi.spyOn(smartCreateApi, "listTemplates").mockResolvedValue([template]);
    const propose = vi.spyOn(smartCreateApi, "propose");

    renderDialog(root);
    clickButton(await findButton("Template"));
    clickButton(await findButton("PRD Template"));
    clickButton(await findButton("Review"));

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Review file");
      expect(document.body).toHaveTextContent("PRD Template");
    });
    expect(propose).not.toHaveBeenCalled();
  });

  it("only offers templates from the invoking drive", async () => {
    vi.spyOn(smartCreateApi, "listTemplates").mockResolvedValue([
      template,
      {
        ...template,
        driveId: "drive-2",
        driveName: "Another drive",
        fileId: "template-2",
        name: "Cross-drive template",
      },
    ]);

    renderDialog(root);
    clickButton(await findButton("Template"));

    const templateButton = await findButton("PRD Template");
    expect(templateButton).toBeEnabled();
    expect(document.body).not.toHaveTextContent("Cross-drive template");
  });

  it("clears incompatible template state when switching to AI mode", async () => {
    vi.spyOn(smartCreateApi, "listTemplates").mockResolvedValue([
      { ...template, categoryPath: [] },
    ]);

    renderDialog(root);
    clickButton(await findButton("Template"));
    clickButton(await findButton("PRD Template"));
    const reviewButton = await findButton("Review");
    expect(reviewButton).toBeEnabled();

    clickButton(await findButton("Describe"));

    const suggestButton = await findButton("Suggest details");
    expect(suggestButton).toBeDisabled();
    expect(document.body).not.toHaveTextContent("Product requirements");
  });

  it("keeps catalog loading independent from authoring-mode changes", async () => {
    const catalog = deferred<Awaited<ReturnType<typeof smartCreateApi.listTemplates>>>();
    vi.spyOn(smartCreateApi, "listTemplates").mockReturnValue(catalog.promise);

    renderDialog(root);
    clickButton(await findButton("Template"));
    catalog.resolve([template]);

    const templateButton = await findButton("PRD Template");
    expect(templateButton).toBeEnabled();
    expect(document.body).not.toHaveTextContent("Looking in Templates…");
  });

  it("cancels pending template discovery when its dialog closes", async () => {
    const catalog = deferred<Awaited<ReturnType<typeof smartCreateApi.listTemplates>>>();
    const loadCatalog = vi.spyOn(smartCreateApi, "listTemplates").mockReturnValue(catalog.promise);
    renderDialog(root);
    const signal = loadCatalog.mock.calls[0]?.[1];
    expect(signal?.aborted).toBe(false);

    clickButton(await findButton("Cancel"));

    expect(signal?.aborted).toBe(true);
    catalog.resolve([]);
  });

  it("shows a retryable template rate limit instead of an empty catalog", async () => {
    vi.spyOn(smartCreateApi, "listTemplates").mockRejectedValue(
      new ApiError(429, "Too many requests. Try again in a minute."),
    );

    renderDialog(root);
    clickButton(await findButton("Template"));

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Too many requests. Try again in a minute.");
      expect(document.body).not.toHaveTextContent("No templates available");
      expect(findButtonNow("Refresh")).toBeEnabled();
    });
  });

  it("locks compose inputs only while the owned proposal is pending", async () => {
    vi.spyOn(smartCreateApi, "listTemplates").mockResolvedValue([]);
    const proposal = deferred<Awaited<ReturnType<typeof smartCreateApi.propose>>>();
    vi.spyOn(smartCreateApi, "propose").mockReturnValue(proposal.promise);

    renderDialog(root);
    typeDescription("A launch brief");
    clickButton(await findButton("Suggest details"));

    expect(document.body.querySelector("textarea")).toBeDisabled();
    proposal.resolve({
      alias: "launch-brief",
      destination: {
        driveId: "drive-1",
        driveName: "Product",
        folderId: "folder-1",
        folderPath: ["Product", "Plans"],
      },
      emoji: "🚀",
      name: "Launch brief",
      source: { fileType: "document", kind: "blank" },
    });

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Review file");
      expect(findButtonNow("Create file")).toBeEnabled();
    });
  });

  it("clears a selected template removed by Drive refresh", async () => {
    vi.spyOn(smartCreateApi, "listTemplates")
      .mockResolvedValueOnce([template])
      .mockResolvedValueOnce([]);

    renderDialog(root);
    clickButton(await findButton("Template"));
    clickButton(await findButton("PRD Template"));
    const reviewButton = await findButton("Review");
    expect(reviewButton).toBeEnabled();
    clickButton(await findButton("Refresh"));

    await vi.waitFor(() => {
      expect(findButtonNow("Review")).toBeDisabled();
      expect(document.body).toHaveTextContent("No templates available");
    });
  });

  it("enqueues once and shows progress while polling the typed task", async () => {
    vi.spyOn(smartCreateApi, "listTemplates").mockResolvedValue([template]);
    const taskId = "55555555-5555-4555-8555-555555555555";
    vi.spyOn(smartCreateApi, "create").mockResolvedValue({ id: taskId, status: "pending" });
    const completion = deferred<Awaited<ReturnType<typeof smartCreateApi.waitForCompletion>>>();
    const waitForCompletion = vi
      .spyOn(smartCreateApi, "waitForCompletion")
      .mockReturnValue(completion.promise);

    renderDialog(root);
    clickButton(await findButton("Template"));
    clickButton(await findButton("PRD Template"));
    clickButton(await findButton("Review"));
    clickButton(await findButton("Create file"));

    await vi.waitFor(() => {
      expect(document.body).toHaveTextContent("Creating in Drive");
      expect(findButtonNow("Creating…")).toBeDisabled();
    });
    expect(waitForCompletion).toHaveBeenCalledWith(
      { id: taskId, status: "pending" },
      expect.any(AbortSignal),
    );

    completion.resolve({
      file: {
        canEdit: true,
        driveId: "drive-1",
        id: "file-1",
        mimeType: MIME_DOC,
        modifiedTime: undefined,
        name: "PRD Template",
        parentFolderId: "folder-1",
      },
      taskId,
      warnings: [],
    });
    await vi.waitFor(() => {
      expect(document.body).not.toHaveTextContent("Creating in Drive");
    });
  });
});

function renderDialog(root: Root) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <SmartCreateDialog
          invokingDriveId="drive-1"
          invokingFolderId="folder-1"
          onOpenChange={vi.fn<(open: boolean) => void>()}
          open
        />
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

async function findButton(text: string): Promise<HTMLButtonElement> {
  return vi.waitFor(() => {
    const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
      (candidate) => candidate.textContent.includes(text),
    );
    if (button === undefined) throw new Error(`Button not found: ${text}`);
    return button;
  });
}

function findButtonNow(text: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
    (candidate) => candidate.textContent.includes(text),
  );
  if (button === undefined) throw new Error(`Button not found: ${text}`);
  return button;
}

function typeDescription(value: string) {
  const textarea = document.body.querySelector("textarea");
  if (textarea === null) throw new Error("Description textarea not found");
  // eslint-disable-next-line @typescript-eslint/unbound-method -- The native setter is immediately rebound to the test element.
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  if (setter === undefined) throw new Error("Textarea value setter not found");
  flushSync(() => {
    setter.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function deferred<T>() {
  const { promise, resolve } = Promise.withResolvers<T>();
  return { promise, resolve };
}
