import { MutationObserver, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";

import { moveFileAction } from "@/app/actions/wiki-actions";
import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { proposeFileReorganization } from "@/lib/reorganize-client";
import { wikiKeys } from "@/lib/wiki-keys";
import { type FileResponse, MIME_DOC } from "@/types/wiki";

import { ReorganizeDocument } from "./reorganize-document";

vi.mock("@/app/actions/wiki-actions", () => ({ moveFileAction: vi.fn() }));
vi.mock("@/lib/reorganize-client", () => ({ proposeFileReorganization: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const MOVED_FILE: FileResponse = {
  canEdit: true,
  driveId: "drive-1",
  id: "file-1",
  mimeType: MIME_DOC,
  modifiedTime: undefined,
  name: "File 1",
  parentFolderId: "folder-2",
};

describe("reorganization move observer admission", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;
  let moved: ReturnType<typeof Promise.withResolvers<ActionResult<FileResponse>>>;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    moved = Promise.withResolvers<ActionResult<FileResponse>>();
    vi.mocked(moveFileAction).mockReset().mockReturnValue(moved.promise);
    vi.mocked(proposeFileReorganization).mockReset().mockResolvedValue(proposalResult());
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    client.clear();
    host.remove();
    vi.restoreAllMocks();
  });

  function render(fileId = "file-1") {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ReorganizeDocument
            driveId="drive-1"
            fileId={fileId}
            fileName={fileId}
            parentFolderId="folder-1"
          />
        </QueryClientProvider>,
      ),
    );
  }

  it("creates no mutation observer until a proposal needs confirmation", async () => {
    const subscribe = vi.spyOn(MutationObserver.prototype, "subscribe");
    const proposal = Promise.withResolvers<ReturnType<typeof proposalResult>>();
    vi.mocked(proposeFileReorganization).mockReturnValue(proposal.promise);
    render();
    expect(subscribe).not.toHaveBeenCalled();
    click("Re-organize");
    expect(subscribe).not.toHaveBeenCalled();
    proposal.resolve(proposalResult());
    await vi.waitFor(() => expect(findButton("Move document")).toBeEnabled());
    expect(subscribe).toHaveBeenCalledOnce();
    click("Keep it here");
    expect(document.body).not.toHaveTextContent("Move this document?");
    expect(moveFileAction).not.toHaveBeenCalled();
  });

  it("keeps an admitted move running after the confirmation owner unmounts", async () => {
    render();
    click("Re-organize");
    await vi.waitFor(() => expect(findButton("Move document")).toBeEnabled());
    click("Move document");
    await vi.waitFor(() => expect(moveFileAction).toHaveBeenCalledOnce());
    expect(moveFileAction).toHaveBeenCalledWith("file-1", "folder-2");
    render("file-2");
    moved.resolve({ data: MOVED_FILE, ok: true });
    await vi.waitFor(() =>
      expect(client.getMutationCache().getAll()[0]?.state.status).toBe("success"),
    );
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("Move this document?");
  });

  it("does not publish a retired move into replacement-session caches or UI", async () => {
    render();
    click("Re-organize");
    await vi.waitFor(() => expect(findButton("Move document")).toBeEnabled());
    click("Move document");
    await vi.waitFor(() => expect(moveFileAction).toHaveBeenCalledOnce());
    retireMutationSession(client);
    render("file-2");
    client.setQueryData(wikiKeys.file("file-2"), "replacement");
    moved.resolve({ data: MOVED_FILE, ok: true });
    await vi.waitFor(() =>
      expect(client.getMutationCache().getAll()[0]?.state.status).toBe("error"),
    );
    expect(client.getQueryData(wikiKeys.file("file-2"))).toBe("replacement");
    expect(client.getQueryData(wikiKeys.file("file-1"))).toBeUndefined();
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });
});

function findButton(text: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
    (candidate) => candidate.textContent.includes(text),
  );
  if (!button) throw new Error(`Button not found: ${text}`);
  return button;
}

function click(text: string) {
  flushSync(() => findButton(text).click());
}

function proposalResult() {
  return {
    currentDestination: {
      driveId: "drive-1",
      driveName: "Workspace",
      folderId: "folder-1",
      folderPath: ["Current"],
    },
    suggestedDestination: {
      driveId: "drive-1",
      driveName: "Workspace",
      folderId: "folder-2",
      folderPath: ["Recommended"],
    },
  };
}
