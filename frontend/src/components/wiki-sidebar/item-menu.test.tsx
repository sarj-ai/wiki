import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { ItemMenu } from "./item-menu";

const MUTATIONS = vi.hoisted(() => ({
  copy: vi.fn(() => ({ mutateAsync: vi.fn() })),
  remove: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
  rename: vi.fn(() => ({ mutateAsync: vi.fn() })),
}));

vi.mock("@/hooks/use-wiki-mutations", () => ({
  useCopyFile: MUTATIONS.copy,
  useDeleteFile: MUTATIONS.remove,
  useRenameFile: MUTATIONS.rename,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn() }),
}));

function menu(fileId = "file-1") {
  return (
    <ItemMenu
      currentName="Example"
      driveId="drive-1"
      fileId={fileId}
      isFolder={false}
      parentFolderId={undefined}
    />
  );
}

describe("file action menu preparation", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    vi.useRealTimers();
  });

  it("renders 500 accessible triggers without creating mutation observers", () => {
    const view = renderToStaticMarkup(
      <>{Array.from({ length: 500 }, (_, index) => menu(`file-${index}`))}</>,
    );
    expect(view.match(/aria-label="Actions for Example"/g)).toHaveLength(500);
    expect(MUTATIONS.copy).not.toHaveBeenCalled();
    expect(MUTATIONS.remove).not.toHaveBeenCalled();
    expect(MUTATIONS.rename).not.toHaveBeenCalled();
  });

  it("opens rename from a prepared menu and preserves its dialog after the menu closes", async () => {
    flushSync(() => root.render(menu()));
    const trigger = host.querySelector("button");
    if (!(trigger instanceof HTMLButtonElement)) throw new TypeError("trigger missing");
    flushSync(() => {
      trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
    });

    await vi.waitFor(() => {
      expect(document.body.textContent).toContain("Rename");
    });
    expect(MUTATIONS.copy).toHaveBeenCalledOnce();
    expect(MUTATIONS.remove).toHaveBeenCalledOnce();
    expect(MUTATIONS.rename).toHaveBeenCalledOnce();

    const rename = [...document.querySelectorAll('[role="menuitem"]')].find(
      (item) => item.textContent === "Rename",
    );
    if (!(rename instanceof HTMLElement)) throw new TypeError("rename action missing");
    flushSync(() => rename.click());
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await vi.waitFor(() => {
      expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Rename file");
    });
    expect(document.querySelector("input")).toHaveValue("Example");
    await vi.advanceTimersByTimeAsync(1_000);
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Rename file");
  });

  it("retires a used idle body after native dismissal and prepares fresh actions on reopening", async () => {
    flushSync(() => root.render(menu()));
    const trigger = host.querySelector("button");
    if (!(trigger instanceof HTMLButtonElement)) throw new TypeError("trigger missing");
    const openMenu = () => {
      flushSync(() => {
        trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
      });
    };
    openMenu();
    await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    const content = document.querySelector('[role="menu"]');
    if (!(content instanceof HTMLElement)) throw new TypeError("Menu content missing");
    flushSync(() => {
      content.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
    });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await vi.advanceTimersByTimeAsync(250);
    expect(document.activeElement).toBe(trigger);
    openMenu();
    await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    expect(MUTATIONS.copy).toHaveBeenCalledTimes(3);
    expect(MUTATIONS.remove).toHaveBeenCalledTimes(3);
    expect(MUTATIONS.rename).toHaveBeenCalledTimes(3);
  });
});
