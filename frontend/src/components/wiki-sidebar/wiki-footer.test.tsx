import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { Sidebar, SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceFrame } from "@/components/workspace-frame";
import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { Preferences } from "@/types/wiki";

import { PreferencesDialogOutletProvider } from "./preferences-dialog-outlet";
import { WikiFooter } from "./wiki-footer";

const ACTIONS = vi.hoisted(() => ({
  logout: vi.fn<() => Promise<void>>(),
  preferences: vi.fn<() => Promise<ActionResult<Preferences>>>(),
  readPreferences: vi.fn<() => Promise<Preferences>>(),
  theme: vi.fn(),
}));
const viewport = vi.hoisted(() => ({ mobile: false }));
vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: () => viewport.mobile }));

vi.mock("@/hooks/use-current-user", () => ({
  useCurrentUser: () => ({
    data: {
      avatarUrl: undefined,
      displayName: "Fixture user",
      email: "fixture@example.test",
      id: "fixture-user",
    },
  }),
}));
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light", setTheme: ACTIONS.theme }),
}));
vi.mock("@/app/actions/wiki-actions", () => ({ setPreferencesAction: ACTIONS.preferences }));
vi.mock("@/lib/auth", () => ({ logout: ACTIONS.logout }));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getPreferences: ACTIONS.readPreferences },
}));

describe("account menu preferences transition", () => {
  let client: QueryClient;
  let container: HTMLDivElement;
  let root: Root;
  let backgroundFrame: HTMLIFrameElement;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    viewport.mobile = false;
    client = new QueryClient({
      defaultOptions: { queries: { gcTime: Infinity, staleTime: Infinity } },
    });
    client.setQueryData(wikiKeys.preferences, { showNonDocFiles: true, hiddenWorkspaceIds: [] });
    client.setQueryData(wikiKeys.drives, []);
    container = document.createElement("div");
    backgroundFrame = document.createElement("iframe");
    backgroundFrame.title = "Fixture embedded view";
    document.body.append(container, backgroundFrame);
    root = createRoot(container);
  });

  function mount(userId = "fixture-user", footerKey = "original") {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <SidebarProvider>
            <PreferencesDialogOutletProvider userId={userId}>
              <Sidebar>
                <WikiFooter key={footerKey} />
              </Sidebar>
            </PreferencesDialogOutletProvider>
            <WorkspaceFrame>
              <span>Fixture workspace</span>
            </WorkspaceFrame>
          </SidebarProvider>
        </QueryClientProvider>,
      ),
    );
  }

  afterEach(() => {
    flushSync(() => root.unmount());
    retireMutationSession(client);
    client.clear();
    container.remove();
    backgroundFrame.remove();
    vi.useRealTimers();
  });

  it.each(["Done", "Close", "Escape"])(
    "closes the account menu before Preferences and restores the trigger after %s",
    async (dismissal) => {
      mount();
      const trigger = container.querySelector<HTMLButtonElement>('[aria-label="Account menu"]');
      if (!trigger) throw new Error("Account trigger missing");
      backgroundFrame.focus();
      expect(document.activeElement).toBe(backgroundFrame);
      trigger.focus();
      flushSync(() => {
        trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
      });
      await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
      const preferences = [...document.querySelectorAll('[role="menuitem"]')].find(
        (item) => item.textContent === "Preferences",
      );
      if (!(preferences instanceof HTMLElement))
        throw new TypeError("Preferences menu item missing");
      flushSync(() => preferences.click());
      await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
      await vi.advanceTimersByTimeAsync(0);
      const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
      if (!dialog) throw new Error("Preferences dialog missing");
      expect(document.querySelector('[role="menu"]')).toBeNull();
      expect(trigger).toHaveAttribute("aria-expanded", "false");
      expect(dialog.contains(document.activeElement)).toBe(true);

      if (dismissal === "Escape") {
        flushSync(() => {
          dialog.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
        });
      } else {
        const close = [...dialog.querySelectorAll("button")].find(
          (button) => button.textContent === dismissal,
        );
        if (!close) throw new Error("Preferences dismissal control missing");
        flushSync(() => close.click());
      }
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      expect(document.querySelector('[role="menu"]')).toBeNull();
      expect(document.activeElement).toBe(trigger);
      expect(ACTIONS.preferences).not.toHaveBeenCalled();
      expect(ACTIONS.theme).not.toHaveBeenCalled();
      expect(ACTIONS.logout).not.toHaveBeenCalled();

      flushSync(() => {
        trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
      });
      await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
    },
  );

  it("keeps Preferences open when the desktop footer is replaced by the phone sidebar", async () => {
    mount();
    const trigger = await openPreferences();
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    viewport.mobile = true;
    mount();
    expect(trigger.isConnected).toBe(false);
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    expect(dialog).toHaveTextContent("Show non-Doc files");
    const done = [...(dialog?.querySelectorAll("button") ?? [])].find(
      (button) => button.textContent === "Done",
    );
    if (!done) throw new Error("Done control missing");
    flushSync(() => done.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(container.querySelector('[data-sidebar="trigger"]'));
    expect(ACTIONS.preferences).not.toHaveBeenCalled();
  });

  it.each(["Done", "Close", "Escape"])(
    "returns %s focus to the new Account menu after phone to expanded desktop",
    async (dismissal) => {
      viewport.mobile = true;
      mount();
      const navigation = container.querySelector<HTMLButtonElement>('[data-sidebar="trigger"]');
      if (!navigation) throw new Error("Navigation trigger missing");
      flushSync(() => navigation.click());
      await vi.advanceTimersByTimeAsync(0);
      const previousTrigger = await openPreferences();
      const dialog = document.querySelector<HTMLElement>(
        '[role="dialog"][data-slot="dialog-content"]',
      );
      if (!dialog) throw new Error("Preferences dialog missing");
      viewport.mobile = false;
      mount();
      await vi.advanceTimersByTimeAsync(0);
      expect(previousTrigger.isConnected).toBe(false);
      expect(document.querySelector('[data-slot="dialog-content"]')).toBe(dialog);
      const nextTrigger = container.querySelector<HTMLButtonElement>('[aria-label="Account menu"]');
      if (!nextTrigger) throw new Error("Replacement Account trigger missing");
      if (dismissal === "Escape") {
        flushSync(() =>
          dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })),
        );
      } else {
        const close = [...dialog.querySelectorAll("button")].find(
          (button) => button.textContent === dismissal,
        );
        if (!close) throw new Error("Preferences dismissal control missing");
        flushSync(() => close.click());
      }
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      expect(document.activeElement).toBe(nextTrigger);
      expect(ACTIONS.preferences).not.toHaveBeenCalled();
    },
  );

  it("retires Preferences when its session aborts or its user changes", async () => {
    mount();
    await openPreferences();
    flushSync(() => retireMutationSession(client));
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    mount("second-user", "second-user");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(ACTIONS.preferences).not.toHaveBeenCalled();
  });

  it("keeps admitted saving feedback through closing and reopening Preferences", async () => {
    const pending = Promise.withResolvers<ActionResult<Preferences>>();
    const saved = { showNonDocFiles: false, hiddenWorkspaceIds: [] };
    ACTIONS.preferences.mockReturnValueOnce(pending.promise);
    ACTIONS.readPreferences.mockResolvedValue(saved);
    try {
      mount();
      await openPreferences();
      const checkbox = document.querySelector<HTMLElement>('[role="checkbox"]');
      if (!checkbox) throw new Error("Preference checkbox missing");
      flushSync(() => checkbox.click());
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="status"]')).toHaveTextContent("Saving preferences…");
      const done = [...document.querySelectorAll("button")].find(
        (button) => button.textContent === "Done",
      );
      if (!done) throw new Error("Preferences Done control missing");
      flushSync(() => done.click());
      await vi.advanceTimersByTimeAsync(1);
      expect(document.querySelector('[data-slot="dialog-content"]')).toBeNull();
      await openPreferences();
      expect(document.querySelector('[role="status"]')).toHaveTextContent("Saving preferences…");
      expect(document.querySelector('[role="checkbox"]')).toHaveAttribute("aria-checked", "false");
      expect(ACTIONS.preferences).toHaveBeenCalledOnce();
      pending.resolve({ ok: true, data: saved });
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="status"]')).toBeNull();
    } finally {
      pending.resolve({ ok: true, data: saved });
      await vi.advanceTimersByTimeAsync(0);
    }
  });
});

async function openPreferences() {
  const trigger = document.querySelector<HTMLButtonElement>('[aria-label="Account menu"]');
  if (!trigger) throw new Error("Account trigger missing");
  trigger.focus();
  flushSync(() =>
    trigger.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" })),
  );
  await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
  const preferences = [...document.querySelectorAll('[role="menuitem"]')].find(
    (item) => item.textContent === "Preferences",
  );
  if (!(preferences instanceof HTMLElement)) throw new TypeError("Preferences menu item missing");
  flushSync(() => preferences.click());
  await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
  await vi.advanceTimersByTimeAsync(0);
  return trigger;
}
