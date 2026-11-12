import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { ApiError } from "@/lib/api-error";
import type { DocumentMode } from "@/lib/file-urls";
import { MIME_DOC, MIME_HTML } from "@/types/wiki";

import { useDocViewerStore } from "./doc-viewer-store";
import { FilePageClient } from "./file-page-client";

const refetchFile = vi.hoisted(() => vi.fn());
const fileQuery = vi.hoisted(() => vi.fn());

vi.mock("./wiki-toolbar", () => ({ WikiToolbar }));
vi.mock("@/hooks/use-wiki-tree", () => ({
  useFile: fileQuery,
}));

function resolvedFile(mimeType = MIME_HTML, id = "html-file") {
  return {
    data: {
      breadcrumb: [],
      canEdit: false,
      driveId: "drive",
      id,
      mimeType,
      modifiedTime: "version-a",
      name: "Page.html",
      parentFolderId: "folder",
    },
    isError: false,
    isFetching: false,
    isPending: false,
    refetch: refetchFile,
  };
}

function WikiToolbar({
  documentMode,
  onDocumentModeChange,
  onReload,
}: {
  documentMode: DocumentMode;
  onDocumentModeChange: (mode: DocumentMode) => void;
  onReload: () => void;
}) {
  return (
    <>
      <button onClick={() => onDocumentModeChange("preview")} type="button">
        Preview
      </button>
      <button onClick={() => onDocumentModeChange("edit")} type="button">
        Edit
      </button>
      <button onClick={onReload} type="button">
        Reload
      </button>
      <span>{documentMode}</span>
    </>
  );
}

function renderFile(root: Root, client: QueryClient, fileId: string) {
  flushSync(() =>
    root.render(
      <QueryClientProvider client={client}>
        <FilePageClient driveId={fileId} />
      </QueryClientProvider>,
    ),
  );
}

describe("document authority and revision checks", () => {
  let root: Root;
  let client: QueryClient;
  let container: HTMLDivElement;

  beforeEach(() => {
    vi.useFakeTimers();
    refetchFile.mockReset();
    fileQuery.mockReset();
    fileQuery.mockReturnValue(resolvedFile());
    useDocViewerStore.getState().reset();
    container = document.createElement("div");
    root = createRoot(container);
    client = new QueryClient();
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    client.clear();
    useDocViewerStore.getState().reset();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("skips hidden-tab polling and keeps checks on the existing visible interval", async () => {
    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    renderFile(root, client, "html-file");
    await vi.advanceTimersByTimeAsync(30 * 60 * 1_000);
    expect(refetchFile).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    await vi.advanceTimersByTimeAsync(5 * 60 * 1_000);
    expect(refetchFile).toHaveBeenCalledOnce();
    flushSync(() => root.render(null));
    await vi.advanceTimersByTimeAsync(5 * 60 * 1_000);
    expect(refetchFile).toHaveBeenCalledOnce();
  });

  it("keeps the mobile reading action aligned with the verified current document", () => {
    fileQuery.mockReturnValue(resolvedFile(MIME_DOC, "doc-a"));
    renderFile(root, client, "doc-a");
    const firstLink = container.querySelector("a");
    expect(firstLink?.textContent).toContain("Read document");
    expect(firstLink?.href).toBe(
      "https://docs.google.com/document/d/doc-a/mobilebasic",
    );
    expect(firstLink?.target).toBe("_blank");
    expect(firstLink?.rel).toBe("noopener noreferrer");
    expect(useDocViewerStore.getState().active?.fileId).toBe("doc-a");

    fileQuery.mockReturnValue(resolvedFile(MIME_DOC, "doc-b"));
    renderFile(root, client, "doc-b");
    expect(container.querySelector("a")?.href).toBe(
      "https://docs.google.com/document/d/doc-b/mobilebasic",
    );
    expect(useDocViewerStore.getState().active?.fileId).toBe("doc-b");
    expect(refetchFile).not.toHaveBeenCalled();
  });

  it("removes the reading action when document authority is pending or access fails", () => {
    fileQuery.mockReturnValue(resolvedFile(MIME_DOC, "doc-a"));
    renderFile(root, client, "doc-a");
    expect(container.querySelector("a")?.textContent).toContain(
      "Read document",
    );

    fileQuery.mockReturnValue({
      ...resolvedFile(MIME_DOC, "doc-a"),
      data: undefined,
      isFetching: true,
      isPending: true,
    });
    renderFile(root, client, "doc-a");
    expect(container.querySelector("a")).toBeNull();
    expect(useDocViewerStore.getState().active).toBeNull();

    fileQuery.mockReturnValue({
      ...resolvedFile(MIME_DOC, "doc-a"),
      data: undefined,
      error: new ApiError(403, "Access denied"),
      isError: true,
    });
    renderFile(root, client, "doc-a");
    expect(container.querySelector('a[href*="mobilebasic"]')).toBeNull();
    expect(useDocViewerStore.getState().active).toBeNull();
  });

  it("switches the verified document, preserves its mode on reload, and resets on navigation", () => {
    const file = resolvedFile(MIME_DOC, "doc-a");
    file.data.canEdit = true;
    fileQuery.mockReturnValue(file);
    renderFile(root, client, "doc-a");
    expect(useDocViewerStore.getState().active?.documentMode).toBe("edit");
    const [preview, edit, reload] = container.querySelectorAll("button");
    if (!preview || !edit || !reload)
      throw new Error("Missing viewer controls");
    flushSync(() => preview.click());
    expect(useDocViewerStore.getState().active?.documentMode).toBe("preview");
    flushSync(() => reload.click());
    expect(useDocViewerStore.getState().active).toMatchObject({
      documentMode: "preview",
      reloadKey: 1,
    });
    flushSync(() => edit.click());
    expect(useDocViewerStore.getState().active?.documentMode).toBe("edit");
    flushSync(() => preview.click());
    fileQuery.mockReturnValue({ ...file, data: { ...file.data, id: "doc-b" } });
    renderFile(root, client, "doc-b");
    expect(useDocViewerStore.getState().active).toMatchObject({
      documentMode: "edit",
      fileId: "doc-b",
    });
  });

  it("forces preview when editing permission is absent or revoked", () => {
    const file = resolvedFile(MIME_DOC, "doc-a");
    file.data.canEdit = true;
    fileQuery.mockReturnValue(file);
    renderFile(root, client, "doc-a");
    expect(useDocViewerStore.getState().active?.documentMode).toBe("edit");
    fileQuery.mockReturnValue({
      ...file,
      data: { ...file.data, canEdit: false },
    });
    renderFile(root, client, "doc-a");
    expect(useDocViewerStore.getState().active?.documentMode).toBe("preview");
  });
});
