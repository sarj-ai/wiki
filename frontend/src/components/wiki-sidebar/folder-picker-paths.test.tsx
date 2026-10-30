import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { SmartDestination } from "@/lib/smart-create-types";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveNode, DriveTree } from "@/types/wiki";
import { MIME_FOLDER } from "@/types/wiki";

import { FolderPickerDialog } from "./folder-picker-dialog";

describe("destination path admission", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;
  const select = vi.fn<(destination: SmartDestination) => void>();
  const close = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    client.setQueryData(wikiKeys.drives, [{ id: "drive", isPersonal: false, name: "Workspace" }]);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    client.clear();
  });

  function render(folders: DriveNode[]) {
    client.setQueryData<DriveTree>(wikiKeys.driveTree("drive"), {
      driveId: "drive",
      files: [],
      filesComplete: true,
      folders,
    });
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <FolderPickerDialog onOpenChange={close} onSelect={select} open />
        </QueryClientProvider>,
      ),
    );
    flushSync(() => button("Expand Workspace").click());
  }

  it("does not construct unused paths for 1,000 collapsed destination rows", () => {
    let reads = 0;
    const folders = Array.from({ length: 1_000 }, (_, index) => ({
      ...node(`folder-${index}`, `Folder ${index}`),
      get name() {
        reads += 1;
        return `Folder ${index}`;
      },
    }));
    render(folders);
    expect(document.querySelectorAll('button[aria-label^="Expand Folder"]')).toHaveLength(1_000);
    expect(reads).toBe(2_000);
    flushSync(() => button("Folder 999").click());
    expect(select).toHaveBeenCalledWith({
      driveId: "drive",
      driveName: "Workspace",
      folderId: "folder-999",
      folderPath: ["Folder 999"],
    });
    expect(close).toHaveBeenCalledWith(false);
  });

  it("has no destination-row path work while the actual dialog content is closed", () => {
    let reads = 0;
    client.setQueryData<DriveTree>(wikiKeys.driveTree("drive"), {
      driveId: "drive",
      files: [],
      filesComplete: true,
      folders: Array.from({ length: 1_000 }, (_, index) => ({
        ...node(`folder-${index}`, `Folder ${index}`),
        get name() {
          reads += 1;
          return `Folder ${index}`;
        },
      })),
    });
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <FolderPickerDialog onOpenChange={close} onSelect={select} open={false} />
        </QueryClientProvider>,
      ),
    );
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(reads).toBe(0);
    expect(select).not.toHaveBeenCalled();
  });

  it("preserves root and nested path selection as branches expand and collapse", () => {
    render([node("root", "Root"), node("child", "Child", "root"), node("leaf", "Leaf", "child")]);
    flushSync(() => button("Expand Root").click());
    flushSync(() => button("Expand Child").click());
    flushSync(() => button("Leaf").click());
    expect(select).toHaveBeenLastCalledWith({
      driveId: "drive",
      driveName: "Workspace",
      folderId: "leaf",
      folderPath: ["Root", "Child", "Leaf"],
    });
    flushSync(() => button("Collapse Child").click());
    flushSync(() => button("Child").click());
    expect(select).toHaveBeenLastCalledWith({
      driveId: "drive",
      driveName: "Workspace",
      folderId: "child",
      folderPath: ["Root", "Child"],
    });
    flushSync(() => button("Workspace").click());
    expect(select).toHaveBeenLastCalledWith({
      driveId: "drive",
      driveName: "Workspace",
      folderId: "drive",
      folderPath: [],
    });
  });
});

function button(text: string): HTMLButtonElement {
  const result = [...document.querySelectorAll("button")].find(
    (item) => item.getAttribute("aria-label") === text || item.textContent === text,
  );
  if (!(result instanceof HTMLButtonElement)) throw new TypeError(`Button ${text} missing`);
  return result;
}

function node(id: string, name: string, parentId?: string): DriveNode {
  return {
    id,
    mimeType: MIME_FOLDER,
    modifiedTime: undefined,
    name,
    parentId,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
