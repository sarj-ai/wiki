import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { SidebarProvider } from "@/components/ui/sidebar";
import type { DriveNode, DriveSummary } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER } from "@/types/wiki";

import { CreateFileMenu } from "./create-file-menu";
import { DriveAccessSheet } from "./drive-access-sheet";
import { DriveSectionProvider, type DriveSectionValue } from "./drive-context";
import { EmojiPickerPopover } from "./emoji-picker-popover";
import { PickableIcon } from "./pickable-icon";
import { TreeNode } from "./tree-node";

const OBSERVERS = vi.hoisted(() => ({
  create: vi.fn(() => ({ mutateAsync: vi.fn() })),
  docIcon: vi.fn(() => ({ mutateAsync: vi.fn() })),
  folderIcon: vi.fn(() => ({ mutateAsync: vi.fn() })),
  folderRead: vi.fn<() => Promise<DriveNode[]>>(),
  permissions: vi.fn(() => ({ data: undefined, isPending: true })),
  theme: vi.fn(() => ({ resolvedTheme: "light" })),
}));

vi.mock("@/hooks/use-wiki-mutations", () => ({
  useCreateFile: OBSERVERS.create,
  useSetDocIcon: OBSERVERS.docIcon,
  useSetFolderIcon: OBSERVERS.folderIcon,
}));
vi.mock("@/hooks/use-wiki-tree", () => ({
  folderFilesQueryOptions: (folderId: string) => ({
    queryFn: OBSERVERS.folderRead,
    queryKey: ["wiki", "folder-files", folderId],
  }),
  useDrivePermissions: OBSERVERS.permissions,
  useFolderFiles: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("@/hooks/use-expanded-folders", () => ({
  useExpandedFolder: () => [false, vi.fn()],
}));
vi.mock("next-themes", () => ({ useTheme: OBSERVERS.theme }));
vi.mock("@emoji-mart/react", () => ({
  default: ({ onEmojiSelect }: { onEmojiSelect: (emoji: { native: string }) => void }) => (
    <button onClick={() => onEmojiSelect({ native: "📚" })} type="button">
      Pick emoji
    </button>
  ),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const DRIVE: DriveSummary = { id: "drive-1", isPersonal: false, name: "Workspace" };

describe("unused sidebar controls", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    OBSERVERS.folderRead.mockResolvedValue([]);
    Object.defineProperty(navigator, "connection", { configurable: true, value: undefined });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    vi.restoreAllMocks();
  });

  it("renders create triggers without preparing mutations or name dialogs", () => {
    const view = renderToStaticMarkup(
      <>
        {Array.from({ length: 500 }, (_, index) => (
          <CreateFileMenu driveId="drive-1" key={index} parentFolderId={`folder-${index}`} />
        ))}
      </>,
    );
    expect(view.match(/aria-label="Create item"/g)).toHaveLength(500);
    expect(OBSERVERS.create).not.toHaveBeenCalled();
  });

  it("keeps a create dialog alive after choosing a type closes the menu", async () => {
    flushSync(() => {
      root.render(<CreateFileMenu driveId="drive-1" parentFolderId="folder-1" />);
    });
    const trigger = buttonIn(host);
    flushSync(() => {
      trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
    });
    await vi.waitFor(() => {
      expect(document.body.textContent).toContain("Smart create…");
    });
    const doc = [...document.querySelectorAll('[role="menuitem"]')].find(
      (item) => item.textContent === "Doc",
    );
    if (!(doc instanceof HTMLElement)) throw new TypeError("Doc action missing");
    flushSync(() => doc.click());
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await vi.waitFor(() => {
      expect(document.querySelector('[role="dialog"]')?.textContent).toContain("New Doc");
    });
    expect(document.querySelector("input")).toHaveValue("Untitled Doc");
  });

  it("creates permission observers only after access is opened", () => {
    flushSync(() => root.render(<DriveAccessSheet drive={DRIVE} />));
    expect(OBSERVERS.permissions).not.toHaveBeenCalled();
    flushSync(() => buttonIn(host).click());
    expect(OBSERVERS.permissions).toHaveBeenCalledWith("drive-1", true);
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Workspace access");
  });

  it("subscribes emoji contents to the theme only after its picker is opened", () => {
    flushSync(() => {
      root.render(
        <EmojiPickerPopover onChange={vi.fn()} value={undefined}>
          <button type="button">Change icon</button>
        </EmojiPickerPopover>,
      );
    });
    expect(OBSERVERS.theme).not.toHaveBeenCalled();
    flushSync(() => buttonIn(host).click());
    expect(OBSERVERS.theme).toHaveBeenCalled();
  });

  it("prepares only the selected icon mutation after opening its picker", () => {
    flushSync(() => {
      root.render(
        <PickableIcon
          ariaLabel="Change folder icon"
          pickable
          target={{ id: "folder-1", kind: "folder" }}
          value={undefined}
        >
          📂
        </PickableIcon>,
      );
    });
    expect(OBSERVERS.docIcon).not.toHaveBeenCalled();
    expect(OBSERVERS.folderIcon).not.toHaveBeenCalled();
    flushSync(() => buttonIn(host).click());
    expect(OBSERVERS.folderIcon).toHaveBeenCalled();
    expect(OBSERVERS.docIcon).not.toHaveBeenCalled();
  });

  it("keeps an in-flight icon change pending across closing and reopening its picker", async () => {
    const change = Promise.withResolvers<undefined>();
    const onChange = vi.fn(async () => await change.promise);
    flushSync(() => {
      root.render(
        <EmojiPickerPopover onChange={onChange} value="🌟">
          <button type="button">Change icon</button>
        </EmojiPickerPopover>,
      );
    });
    const trigger = buttonIn(host);
    flushSync(() => trigger.click());
    await vi.waitFor(() => expect(buttonWithText("Pick emoji")).toBeDefined());
    flushSync(() => buttonWithText("Pick emoji").click());
    flushSync(() => trigger.click());
    flushSync(() => trigger.click());
    expect(buttonWithText("Remove icon")).toBeDisabled();
    expect(onChange).toHaveBeenCalledOnce();
    change.resolve(undefined);
    await vi.waitFor(() => expect(buttonWithText("Remove icon")).toBeEnabled());
  });

  it("retains a collapsed folder's count without sorting its 10,000 children", () => {
    const queryClient = new QueryClient();
    const children = Array.from({ length: 10_000 }, (_, index) =>
      node(`file-${index}`, `Document ${10_000 - index}`),
    );
    const value: DriveSectionValue = {
      activeDriveId: null,
      childrenByParent: new Map(),
      docIconOverrides: new Map(),
      driveId: "drive-1",
      filesByParent: new Map([["folder-1", children]]),
      iconOverrides: new Map(),
      optimisticByContainer: new Map(),
      showNonDocFiles: true,
      sortOrderMap: new Map(),
      treeFilesComplete: true,
    };
    const comparisons = vi.spyOn(String.prototype, "localeCompare");
    try {
      const view = renderToStaticMarkup(
        <QueryClientProvider client={queryClient}>
          <SidebarProvider>
            <DriveSectionProvider value={value}>
              <TreeNode
                depth={0}
                node={{ ...node("folder-1", "Folder"), mimeType: MIME_FOLDER }}
                parentFolderId={undefined}
              />
            </DriveSectionProvider>
          </SidebarProvider>
        </QueryClientProvider>,
      );
      expect(view).toContain("10000");
      expect(view).not.toContain("Document 10000");
      expect(comparisons).not.toHaveBeenCalled();
    } finally {
      comparisons.mockRestore();
      queryClient.clear();
    }
  });

  it.each(["healthy", "save-data", "hidden", "touch"])(
    "prefetches collapsed folders only for eligible visible intent: %s",
    async (mode) => {
      vi.useFakeTimers();
      const queryClient = new QueryClient();
      const value: DriveSectionValue = {
        activeDriveId: null,
        childrenByParent: new Map(),
        docIconOverrides: new Map(),
        driveId: "drive-1",
        filesByParent: new Map(),
        iconOverrides: new Map(),
        optimisticByContainer: new Map(),
        showNonDocFiles: true,
        sortOrderMap: new Map(),
        treeFilesComplete: false,
      };
      if (mode === "save-data") {
        Object.defineProperty(navigator, "connection", {
          configurable: true,
          value: { saveData: true },
        });
      }
      if (mode === "hidden") {
        vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      }
      try {
        flushSync(() => {
          root.render(
            <QueryClientProvider client={queryClient}>
              <SidebarProvider>
                <DriveSectionProvider value={value}>
                  <TreeNode
                    depth={0}
                    node={{ ...node("folder-1", "Folder"), mimeType: MIME_FOLDER }}
                    parentFolderId={undefined}
                  />
                </DriveSectionProvider>
              </SidebarProvider>
            </QueryClientProvider>,
          );
        });
        const folder = host.querySelector('[data-file-id="folder-1"]');
        if (!(folder instanceof HTMLElement)) throw new TypeError("folder row missing");
        folder.dispatchEvent(
          new PointerEvent("pointerover", {
            bubbles: true,
            pointerType: mode === "touch" ? "touch" : "mouse",
          }),
        );
        await vi.advanceTimersByTimeAsync(80);
        expect(OBSERVERS.folderRead).toHaveBeenCalledTimes(mode === "healthy" ? 1 : 0);
      } finally {
        queryClient.clear();
        vi.useRealTimers();
      }
    },
  );
});

function buttonIn(host: HTMLElement): HTMLButtonElement {
  const button = host.querySelector("button");
  if (!(button instanceof HTMLButtonElement)) throw new TypeError("button missing");
  return button;
}

function buttonWithText(text: string): HTMLButtonElement {
  const button = [...document.querySelectorAll("button")].find((item) => item.textContent === text);
  if (!(button instanceof HTMLButtonElement)) throw new TypeError(`button ${text} missing`);
  return button;
}

function node(id: string, name: string): DriveNode {
  return {
    id,
    mimeType: MIME_DOC,
    modifiedTime: undefined,
    name,
    parentId: "folder-1",
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
  };
}
