import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { FileDetail } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

import { ItemMenu } from "./item-menu";

const ACTIONS = vi.hoisted(() => ({ rename: vi.fn<() => Promise<ActionResult<FileDetail>>>() }));
vi.mock("@/app/actions/wiki-actions", () => ({
  renameFileAction: ACTIONS.rename,
  copyFileAction: vi.fn(),
  deleteFileAction: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("next/dynamic", async () => ({
  default: await vi.importActual("next/dist/shared/lib/app-dynamic"),
}));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getDrives: vi.fn().mockResolvedValue([]) },
}));

const FILE: FileDetail = {
  breadcrumb: [],
  canEdit: false,
  driveId: undefined,
  driveName: undefined,
  id: "file",
  mimeType: MIME_DOC,
  modifiedTime: "v1",
  name: "Provider old name",
  parentFolderId: undefined,
};

function submitRename() {
  const input = document.querySelector("input");
  if (!(input instanceof HTMLInputElement)) throw new TypeError("Name input missing");
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.bind(
    input,
  );
  if (!setter) throw new Error("Native input setter missing");
  flushSync(() => {
    setter("Rename requested");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const form = document.querySelector("form");
  if (!form) throw new Error("Name prompt form missing");
  flushSync(() => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

describe("used item-action body lifetime", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    ACTIONS.rename.mockReset();
    client = new QueryClient({
      defaultOptions: {
        mutations: { retry: false, gcTime: Infinity },
        queries: { retry: false, gcTime: Infinity },
      },
    });
    client.setQueryData(wikiKeys.file(FILE.id), FILE);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    retireMutationSession(client);
    client.clear();
    await vi.advanceTimersByTimeAsync(0);
    host.remove();
    vi.useRealTimers();
  });

  function renderMenu() {
    flushSync(() =>
      root.render(
        <StrictMode>
          <QueryClientProvider client={client}>
            <ItemMenu
              currentName={FILE.name}
              driveId={undefined}
              fileId={FILE.id}
              isFolder={false}
              parentFolderId={undefined}
            />
          </QueryClientProvider>
        </StrictMode>,
      ),
    );
  }

  async function openRename() {
    renderMenu();
    const trigger = host.querySelector("button");
    if (!(trigger instanceof HTMLButtonElement)) throw new TypeError("Menu trigger missing");
    flushSync(() => {
      trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
    });
    await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    const action = [...document.querySelectorAll('[role="menuitem"]')].find(
      (item) => item.textContent === "Rename",
    );
    if (!(action instanceof HTMLElement)) throw new TypeError("Rename action missing");
    flushSync(() => action.click());
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
  }

  it.each(["Rename", "Duplicate", "Move to…", "Move to trash"])(
    "returns focus to the persistent action trigger after cancelling %s without a write",
    async (label) => {
      renderMenu();
      const trigger = host.querySelector("button");
      if (!(trigger instanceof HTMLButtonElement)) throw new TypeError("Menu trigger missing");
      flushSync(() => {
        trigger.focus();
        trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
      });
      await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
      const action = [...document.querySelectorAll('[role="menuitem"]')].find(
        (item) => item.textContent === label,
      );
      if (!(action instanceof HTMLElement)) throw new TypeError("Action missing");
      flushSync(() => action.click());
      await vi.waitFor(() =>
        expect(document.querySelector('[role="dialog"], [role="alertdialog"]')).not.toBeNull(),
      );
      await vi.advanceTimersByTimeAsync(250);
      const cancel = [...document.querySelectorAll("button")].find(
        (button) => button.textContent === "Cancel",
      );
      if (!(cancel instanceof HTMLButtonElement)) throw new TypeError("Cancel missing");
      flushSync(() => cancel.click());
      await vi.advanceTimersByTimeAsync(250);
      expect(document.querySelector('[role="dialog"], [role="alertdialog"]')).toBeNull();
      expect(trigger).toHaveFocus();
      expect(client.getMutationCache().getAll()).toHaveLength(0);
      expect(ACTIONS.rename).not.toHaveBeenCalled();
    },
  );

  it("retains a selected rename across menu dismissal and a delayed provider acknowledgment", async () => {
    const pending = Promise.withResolvers<ActionResult<FileDetail>>();
    ACTIONS.rename.mockReturnValue(pending.promise);
    await openRename();
    submitRename();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(ACTIONS.rename).toHaveBeenCalledOnce();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Rename file");
    expect(client.getQueryData<FileDetail>(wikiKeys.file(FILE.id))?.name).toBe(FILE.name);
    pending.resolve({ data: { ...FILE, name: "Rename requested" }, ok: true });
    await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull());
    expect(client.getQueryData<FileDetail>(wikiKeys.file(FILE.id))?.name).toBe("Rename requested");
    await vi.advanceTimersByTimeAsync(250);
    await openRename();
    expect(document.querySelector("input")).toHaveValue(FILE.name);
  });

  it("keeps an admitted provider write running after native dismissal and retry after denial", async () => {
    const pending = Promise.withResolvers<ActionResult<FileDetail>>();
    ACTIONS.rename.mockReturnValue(pending.promise);
    await openRename();
    submitRename();
    await vi.advanceTimersByTimeAsync(0);
    const dialog = document.querySelector('[role="dialog"]');
    if (!(dialog instanceof HTMLElement)) throw new TypeError("Name dialog missing");
    flushSync(() => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
    });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(ACTIONS.rename).toHaveBeenCalledOnce();
    expect(client.getMutationCache().getAll()[0]?.state.status).toBe("pending");
    pending.resolve({ error: "Permission denied", ok: false, status: 403 });
    await vi.waitFor(() =>
      expect(client.getMutationCache().getAll()[0]?.state.status).toBe("error"),
    );
    await vi.advanceTimersByTimeAsync(250);
    ACTIONS.rename.mockResolvedValue({ data: { ...FILE, name: "Rename requested" }, ok: true });
    await openRename();
    submitRename();
    await vi.waitFor(() =>
      expect(client.getQueryData<FileDetail>(wikiKeys.file(FILE.id))?.name).toBe(
        "Rename requested",
      ),
    );
    expect(ACTIONS.rename).toHaveBeenCalledTimes(2);
  });
});
