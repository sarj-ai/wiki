import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { wikiKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import { type DriveTree, MIME_FOLDER } from "@/types/wiki";

import { MoveFileDialog } from "./move-file-dialog";

const MOVE = vi.hoisted(() => vi.fn());
vi.mock("@/app/actions/wiki-actions", () => ({ moveFileAction: MOVE }));
vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getDriveFolders: vi.fn() } }));

it("recovers an expanded drive's failed folder read without issuing a move", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({
    defaultOptions: { queries: { gcTime: Infinity, retry: false, staleTime: Infinity } },
  });
  client.setQueryData(wikiKeys.drives, [
    { id: "destination", isPersonal: false, name: "Destination" },
  ]);
  const tree: DriveTree = {
    driveId: "destination",
    files: [],
    filesComplete: true,
    folders: [
      {
        id: "folder",
        name: "Recovered folder",
        mimeType: MIME_FOLDER,
        modifiedTime: undefined,
        parentId: undefined,
        shortcutInaccessible: false,
        shortcutTargetDriveId: undefined,
        shortcutTargetDriveName: undefined,
        shortcutTargetId: undefined,
        shortcutTargetMimeType: undefined,
      },
    ],
  };
  vi.mocked(WIKI_READ_API.getDriveFolders)
    .mockRejectedValueOnce(new Error("Read unavailable"))
    .mockResolvedValueOnce(tree);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <MoveFileDialog
            currentDriveId="source"
            currentParentFolderId={undefined}
            fileId="file"
            fileName="Plan"
            onOpenChange={vi.fn()}
            open
          />
        </QueryClientProvider>,
      ),
    );
    const expand = document.querySelector<HTMLButtonElement>(
      'button[aria-label="Expand Destination"]',
    );
    if (!expand) throw new Error("Destination expansion missing");
    flushSync(() => expand.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="alert"]')).toHaveTextContent(
      "Folders could not be loaded.",
    );
    const retry = [...document.querySelectorAll("button")].find(
      (button) => button.textContent === "Retry folders",
    );
    if (!retry) throw new Error("Folder retry missing");
    flushSync(() => retry.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(document.body).toHaveTextContent("Recovered folder");
    expect(WIKI_READ_API.getDriveFolders).toHaveBeenCalledTimes(2);
    expect(MOVE).not.toHaveBeenCalled();
  } finally {
    flushSync(() => root.unmount());
    client.clear();
    container.remove();
    vi.useRealTimers();
  }
});
