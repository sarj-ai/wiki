import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { proposeFileReorganization } from "@/lib/reorganize-client";

import { ReorganizeDocument } from "./reorganize-document";

const MutationMock = vi.hoisted(() => ({
  isPending: false,
  mutateAsync: vi.fn(),
}));

vi.mock("@/hooks/use-wiki-mutations", () => ({
  useMoveFile: () => MutationMock,
}));
vi.mock("@/lib/reorganize-client", () => ({ proposeFileReorganization: vi.fn() }));

describe("ReorganizeDocument", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    MutationMock.isPending = false;
    MutationMock.mutateAsync.mockReset();
    vi.mocked(proposeFileReorganization).mockReset();
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
  });

  it("coalesces same-tick proposal and move clicks", async () => {
    const proposal = Promise.withResolvers<ReturnType<typeof proposalResult>>();
    vi.mocked(proposeFileReorganization).mockReturnValue(proposal.promise);
    const moved = Promise.withResolvers<unknown>();
    MutationMock.mutateAsync.mockReturnValue(moved.promise);
    render(root, "file-1");

    const reorganize = findButton("Re-organize");
    click(reorganize);
    click(reorganize);
    expect(proposeFileReorganization).toHaveBeenCalledOnce();

    proposal.resolve(proposalResult());
    await vi.waitFor(() => expect(findButton("Move document")).toBeEnabled());
    const move = findButton("Move document");
    click(move);
    click(move);
    expect(MutationMock.mutateAsync).toHaveBeenCalledOnce();
    moved.resolve(undefined);
  });

  it("discards a proposal that resolves after navigation", async () => {
    const proposal = Promise.withResolvers<ReturnType<typeof proposalResult>>();
    vi.mocked(proposeFileReorganization).mockReturnValue(proposal.promise);
    render(root, "file-1");
    click(findButton("Re-organize"));

    render(root, "file-2");
    proposal.resolve(proposalResult());

    await vi.waitFor(() => {
      expect(document.body).not.toHaveTextContent("Move this document?");
    });
    expect(MutationMock.mutateAsync).not.toHaveBeenCalled();
  });
});

function render(root: Root, fileId: string) {
  flushSync(() => {
    root.render(
      <ReorganizeDocument
        driveId="drive-1"
        fileId={fileId}
        fileName={`${fileId} name`}
        parentFolderId="folder-1"
      />,
    );
  });
}

function click(button: HTMLButtonElement) {
  flushSync(() => button.click());
}

function findButton(text: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
    (candidate) => candidate.textContent.includes(text),
  );
  if (button === undefined) throw new Error(`Button not found: ${text}`);
  return button;
}

function proposalResult() {
  return {
    currentDestination: {
      driveId: "drive-1",
      driveName: "Workspace",
      folderId: "folder-1",
      folderPath: ["Current"],
    },
    reason: "The destination is a better match.",
    suggestedDestination: {
      driveId: "drive-1",
      driveName: "Workspace",
      folderId: "folder-2",
      folderPath: ["Recommended"],
    },
  };
}
