import Link from "next/link";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { Sidebar, SidebarProvider, SidebarTrigger } from "./sidebar";

const viewport = vi.hoisted(() => ({ mobile: true }));
const COOKIE_WRITE = vi.fn().mockResolvedValue(undefined);
vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: () => viewport.mobile }));

describe("responsive sidebar ownership", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    viewport.mobile = true;
    COOKIE_WRITE.mockClear();
    vi.useFakeTimers();
    vi.stubGlobal("cookieStore", { set: COOKIE_WRITE });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    await vi.advanceTimersByTimeAsync(0);
    host.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function mount(persistState?: boolean) {
    const persistence = persistState === undefined ? {} : { persistState };
    flushSync(() =>
      root.render(
        <SidebarProvider {...persistence}>
          <Sidebar>
            <Link href="/">Recent pages</Link>
          </Sidebar>
          <SidebarTrigger />
        </SidebarProvider>,
      ),
    );
  }

  function trigger() {
    const element = host.querySelector<HTMLButtonElement>('[data-sidebar="trigger"]');
    if (!element) throw new Error("Sidebar trigger missing");
    return element;
  }

  it("provides a close button and restores the mobile trigger after dismissal", async () => {
    mount();
    trigger().focus();
    flushSync(() => trigger().click());
    expect(trigger()).toHaveAttribute("aria-expanded", "true");
    const dialog = document.querySelector('[role="dialog"]');
    const close = [...(dialog?.querySelectorAll("button") ?? [])].find(
      (button) => button.textContent === "Close",
    );
    if (!close) throw new Error("Visible mobile sidebar close control missing");
    flushSync(() => close.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(trigger());
  });

  it("does not resurrect the mobile drawer over another dialog after a desktop round trip", async () => {
    mount();
    flushSync(() => trigger().click());
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    viewport.mobile = false;
    mount();
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    viewport.mobile = true;
    mount();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    flushSync(() => trigger().click());
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("keeps normal desktop sidebar preference persistence enabled by default", () => {
    viewport.mobile = false;
    mount();
    flushSync(() => trigger().click());
    expect(COOKIE_WRITE).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        name: "sidebar_state",
        path: "/",
        value: "false",
      }),
    );
  });

  it("preserves existing preferences when an isolated sidebar is toggled or its shortcut is used", () => {
    viewport.mobile = false;
    mount(false);
    flushSync(() => trigger().click());
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    flushSync(() =>
      window.dispatchEvent(new KeyboardEvent("keydown", { ctrlKey: true, key: "b" })),
    );
    expect(trigger()).toHaveAttribute("aria-expanded", "true");
    flushSync(() =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "b", metaKey: true })),
    );
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(COOKIE_WRITE).not.toHaveBeenCalled();
  });
});
