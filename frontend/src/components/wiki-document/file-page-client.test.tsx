import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { MIME_HTML } from "@/types/wiki";

import { useDocViewerStore } from "./doc-viewer-store";
import { FilePageClient } from "./file-page-client";

const refetchFile = vi.hoisted(() => vi.fn());

vi.mock("./wiki-toolbar", () => ({ WikiToolbar }));
vi.mock("@/hooks/use-wiki-tree", () => ({
  useFile: () => ({
    data: {
      breadcrumb: [],
      canEdit: false,
      driveId: "drive",
      id: "html-file",
      mimeType: MIME_HTML,
      modifiedTime: "version-a",
      name: "Page.html",
      parentFolderId: "folder",
    },
    isError: false,
    isFetching: false,
    isPending: false,
    refetch: refetchFile,
  }),
}));

function WikiToolbar() {
  return <span />;
}

describe("active HTML document revision checks", () => {
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    refetchFile.mockReset();
    useDocViewerStore.getState().reset();
    root = createRoot(document.createElement("div"));
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
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <FilePageClient driveId="html-file" />
        </QueryClientProvider>,
      ),
    );
    await vi.advanceTimersByTimeAsync(30 * 60 * 1_000);
    expect(refetchFile).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    await vi.advanceTimersByTimeAsync(5 * 60 * 1_000);
    expect(refetchFile).toHaveBeenCalledOnce();
    flushSync(() => root.render(null));
    await vi.advanceTimersByTimeAsync(5 * 60 * 1_000);
    expect(refetchFile).toHaveBeenCalledOnce();
  });
});
