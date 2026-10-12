import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { Preferences } from "@/types/wiki";

import { WikiFooter } from "./wiki-footer";

const ACTIONS = vi.hoisted(() => ({
  logout: vi.fn<() => Promise<void>>(),
  preferences: vi.fn<() => Promise<ActionResult<Preferences>>>(),
  theme: vi.fn(),
}));

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

describe("account menu preferences transition", () => {
  let client: QueryClient;
  let container: HTMLDivElement;
  let root: Root;
  let backgroundFrame: HTMLIFrameElement;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
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
      flushSync(() =>
        root.render(
          <QueryClientProvider client={client}>
            <WikiFooter />
          </QueryClientProvider>,
        ),
      );
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
      if (!(preferences instanceof HTMLElement)) throw new Error("Preferences menu item missing");
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
});
