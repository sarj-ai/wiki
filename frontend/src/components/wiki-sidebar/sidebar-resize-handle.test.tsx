import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import type { MockInstance } from "vitest";

import { SIDEBAR_WIDTH_COOKIE } from "@/lib/storage-schemas";

import { SidebarResizeHandle } from "./sidebar-resize-handle";

function clearWidthCookie() {
  // eslint-disable-next-line unicorn/no-document-cookie -- Exercise the same SSR-readable preference cookie as the resize handle.
  document.cookie = `${SIDEBAR_WIDTH_COOKIE}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/`;
}

describe("sidebar resize ownership", () => {
  let root: Root;
  let host: HTMLDivElement;
  let wrapper: HTMLDivElement;
  let pane: HTMLDivElement;
  let contentInset: number;
  let gap: HTMLDivElement;
  let frames: Map<number, FrameRequestCallback>;
  let nextFrame: number;
  let add: MockInstance<typeof window.addEventListener>;
  let remove: MockInstance<typeof window.removeEventListener>;
  let elementAdd: MockInstance<typeof HTMLElement.prototype.addEventListener>;
  let elementRemove: MockInstance<typeof HTMLElement.prototype.removeEventListener>;
  let cookieWrite: MockInstance<(value: string) => void>;

  beforeEach(() => {
    frames = new Map();
    nextFrame = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.set(++nextFrame, callback);
      return nextFrame;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
    add = vi.spyOn(window, "addEventListener");
    remove = vi.spyOn(window, "removeEventListener");
    elementAdd = vi.spyOn(HTMLElement.prototype, "addEventListener");
    elementRemove = vi.spyOn(HTMLElement.prototype, "removeEventListener");
    host = document.createElement("div");
    wrapper = document.createElement("div");
    wrapper.dataset["slot"] = "sidebar-wrapper";
    pane = document.createElement("div");
    pane.dataset["slot"] = "sidebar";
    pane.dataset["side"] = "left";
    contentInset = 1;
    const container = document.createElement("div");
    container.dataset["slot"] = "sidebar-container";
    vi.spyOn(container, "getBoundingClientRect").mockImplementation(
      () =>
        new DOMRect(
          0,
          0,
          Number.parseFloat(wrapper.style.getPropertyValue("--sidebar-width")) || 300,
          0,
        ),
    );
    const inner = document.createElement("div");
    inner.dataset["slot"] = "sidebar-inner";
    vi.spyOn(inner, "getBoundingClientRect").mockImplementation(
      () =>
        new DOMRect(
          0,
          0,
          (Number.parseFloat(wrapper.style.getPropertyValue("--sidebar-width")) || 300) -
            contentInset,
          0,
        ),
    );
    gap = document.createElement("div");
    gap.dataset["slot"] = "sidebar-gap";
    gap.style.transition = "width 200ms";
    inner.append(host);
    container.append(inner);
    pane.append(gap, container);
    wrapper.append(pane);
    document.body.append(wrapper);
    document.body.style.cursor = "crosshair";
    document.body.style.userSelect = "text";
    clearWidthCookie();
    cookieWrite = vi.spyOn(document, "cookie", "set");
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    // Keep a broken baseline test from leaking its global handlers into later tests.
    for (const [type, listener, options] of add.mock.calls) {
      if (type.startsWith("pointer")) window.removeEventListener(type, listener, options);
    }
    wrapper.remove();
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    clearWidthCookie();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function start(pointerId = 1) {
    if (!host.querySelector('[role="separator"]')) {
      flushSync(() => root.render(<SidebarResizeHandle />));
    }
    const separator = host.querySelector('[role="separator"]');
    flushSync(() =>
      separator?.dispatchEvent(
        new PointerEvent("pointerdown", {
          bubbles: true,
          clientX: 100,
          pointerId,
        }),
      ),
    );
  }

  function send(type: string, clientX: number, pointerId = 1) {
    flushSync(() => window.dispatchEvent(new PointerEvent(type, { clientX, pointerId })));
  }

  function mountHandle(strict = false) {
    flushSync(() =>
      root.render(
        strict ? (
          <StrictMode>
            <SidebarResizeHandle />
          </StrictMode>
        ) : (
          <SidebarResizeHandle />
        ),
      ),
    );
    return findSeparator();
  }

  function findSeparator() {
    const separator = host.querySelector<HTMLDivElement>('[role="separator"]');
    if (!separator) throw new Error("Resize separator missing");
    return separator;
  }

  function press(key: string, modifiers: KeyboardEventInit = {}) {
    const event = new KeyboardEvent("keydown", {
      bubbles: true,
      cancelable: true,
      key,
      ...modifiers,
    });
    const separator = findSeparator();
    flushSync(() => separator.dispatchEvent(event));
    return event;
  }

  function active(type: string) {
    const removed = remove.mock.calls.filter(([event]) => event === type);
    return add.mock.calls.filter(
      ([event, handler]) => event === type && !removed.some(([, released]) => handler === released),
    ).length;
  }

  function activeElementListeners(separator: HTMLElement, eventType: string) {
    const attached = elementAdd.mock.calls.filter(
      ([type], index) => type === eventType && elementAdd.mock.contexts[index] === separator,
    ).length;
    const retired = elementRemove.mock.calls.filter(
      ([type], index) => type === eventType && elementRemove.mock.contexts[index] === separator,
    ).length;
    return attached - retired;
  }

  function runFrames() {
    const pending = [...frames];
    frames.clear();
    for (const [, callback] of pending) callback(0);
  }

  function expectReleased() {
    expect({
      frames: frames.size,
      moveListeners: active("pointermove"),
      upListeners: active("pointerup"),
      cancelListeners: active("pointercancel"),
      cursor: document.body.style.cursor,
      userSelect: document.body.style.userSelect,
      transition: gap.style.transition,
    }).toEqual({
      frames: 0,
      moveListeners: 0,
      upListeners: 0,
      cancelListeners: 0,
      cursor: "crosshair",
      userSelect: "text",
      transition: "width 200ms",
    });
  }

  it("accumulates rapid keyboard steps while the rendered width is still animating", () => {
    const sidebar = wrapper.querySelector<HTMLElement>('[data-slot="sidebar-container"]');
    if (!sidebar) throw new Error("Sidebar container missing");
    vi.spyOn(sidebar, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 300, 800));
    wrapper.style.setProperty("--sidebar-width", "300px");
    const separator = mountHandle();
    press("ArrowRight");
    press("ArrowRight");
    press("ArrowRight");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("348px");
    expect(separator).toHaveAttribute("aria-valuenow", "348");
    press("ArrowLeft");
    press("ArrowLeft");
    press("ArrowLeft");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("300px");
    expect(separator).toHaveAttribute("aria-valuenow", "300");
  });

  it.each(["left", "right"])(
    "retains the intended %s width when a pointer takes over an animated keyboard resize",
    (side) => {
      const sidebar = wrapper.querySelector<HTMLElement>('[data-slot="sidebar-container"]');
      if (!sidebar) throw new Error("Sidebar container missing");
      vi.spyOn(sidebar, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 300, 800));
      pane.dataset["side"] = side;
      wrapper.style.setProperty("--sidebar-width", "300px");
      mountHandle();
      const growKey = side === "left" ? "ArrowRight" : "ArrowLeft";
      press(growKey);
      press(growKey);
      press(growKey);
      expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("348px");
      start();
      send("pointerup", side === "left" ? 110 : 90);
      expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("358px");
      expectReleased();
    },
  );

  it("provides a focusable width range and persists bounded keyboard adjustments", () => {
    const separator = mountHandle();
    expect(separator.tabIndex).toBe(0);
    flushSync(() => separator.focus());
    expect(document.activeElement).toBe(separator);
    expect(separator).toHaveAccessibleName("Resize sidebar");
    expect(separator).toHaveAttribute("aria-orientation", "vertical");
    expect(separator).toHaveAttribute("aria-valuemin", "224");
    expect(separator).toHaveAttribute("aria-valuemax", "576");
    expect(separator).toHaveAttribute("aria-valuenow", "300");

    expect(press("ArrowRight").defaultPrevented).toBe(true);
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("316px");
    expect(separator).toHaveAttribute("aria-valuenow", "316");
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE}=316px`);
    press("ArrowLeft");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("300px");
    press("Home");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("224px");
    for (let count = 0; count < 4; count++) press("ArrowLeft", { repeat: true });
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("224px");
    press("End");
    for (let count = 0; count < 4; count++) press("ArrowRight", { repeat: true });
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("576px");
    expect(separator).toHaveAttribute("aria-valuenow", "576");
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE}=576px`);
    expect(document.activeElement).toBe(separator);
    expect(host.querySelector('[aria-hidden="true"]')).toBeNull();
    expectReleased();
  });

  it.each([1, 16])("measures the whole pane when border or padding consumes %s pixels", (inset) => {
    contentInset = inset;
    const separator = mountHandle();
    expect(separator).toHaveAttribute("aria-valuenow", "300");
    for (let count = 0; count < 3; count++) press("ArrowRight");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("348px");
    for (let count = 0; count < 3; count++) press("ArrowLeft");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("300px");
    start();
    send("pointerup", 132);
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("332px");
    expect(separator).toHaveAttribute("aria-valuenow", "332");
    expectReleased();
  });

  it.each([false, true])(
    "retires its keyboard listener after native focus and rerenders (strict=%s)",
    (strict) => {
      const separator = mountHandle(strict);
      expect(activeElementListeners(separator, "keydown")).toBe(1);
      expect(activeElementListeners(separator, "focus")).toBe(1);
      flushSync(() => separator.focus());
      cookieWrite.mockClear();
      press("ArrowRight");
      mountHandle(strict);
      expect(activeElementListeners(separator, "keydown")).toBe(1);
      expect(activeElementListeners(separator, "focus")).toBe(1);
      expect(cookieWrite).toHaveBeenCalledOnce();
      expect(document.activeElement).toBe(separator);
      const previousWidth = wrapper.style.getPropertyValue("--sidebar-width");
      const previousCookie = document.cookie;
      flushSync(() => root.unmount());
      expect(activeElementListeners(separator, "keydown")).toBe(0);
      expect(activeElementListeners(separator, "focus")).toBe(0);
      separator.dispatchEvent(new KeyboardEvent("keydown", { key: "End" }));
      expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe(previousWidth);
      expect(document.cookie).toBe(previousCookie);
      expect(cookieWrite).toHaveBeenCalledOnce();

      root = createRoot(host);
      const replacement = mountHandle(strict);
      expect(replacement).not.toBe(separator);
      expect(activeElementListeners(replacement, "keydown")).toBe(1);
      expect(activeElementListeners(replacement, "focus")).toBe(1);
      flushSync(() => replacement.focus());
      cookieWrite.mockClear();
      separator.dispatchEvent(new KeyboardEvent("keydown", { key: "Home" }));
      expect(cookieWrite).not.toHaveBeenCalled();
      press("ArrowLeft");
      expect(replacement).toHaveAttribute("aria-valuenow", "300");
      expect(document.activeElement).toBe(replacement);
      expect(cookieWrite).toHaveBeenCalledOnce();
    },
  );

  it("hydrates a server separator using the actual mounted pane width", async () => {
    flushSync(() => root.unmount());
    const parsed = new DOMParser().parseFromString(
      renderToString(<SidebarResizeHandle />),
      "text/html",
    );
    host.replaceChildren(...parsed.body.childNodes);
    const serverSeparator = findSeparator();
    expect(serverSeparator.tabIndex).toBe(-1);
    expect(serverSeparator).not.toHaveAttribute("aria-valuenow");
    const recoverable = vi.fn();
    root = hydrateRoot(host, <SidebarResizeHandle />, { onRecoverableError: recoverable });
    await vi.waitFor(() => {
      expect(findSeparator().tabIndex).toBe(0);
      expect(findSeparator()).toHaveAttribute("aria-valuenow", "300");
    });
    expect(recoverable).not.toHaveBeenCalled();
    press("ArrowRight");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("316px");
    expect(activeElementListeners(findSeparator(), "keydown")).toBe(1);
    expect(activeElementListeners(findSeparator(), "focus")).toBe(1);
  });

  it("leaves unrelated and modified key commands unchanged", () => {
    mountHandle();
    for (const key of ["ArrowUp", "ArrowDown", "Enter", "Tab", "Escape", "a"]) {
      expect(press(key).defaultPrevented).toBe(false);
    }
    for (const modifiers of [{ altKey: true }, { ctrlKey: true }, { metaKey: true }]) {
      expect(press("ArrowRight", modifiers).defaultPrevented).toBe(false);
    }
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("");
    expect(document.cookie).not.toContain(SIDEBAR_WIDTH_COOKIE);
    expectReleased();
  });

  it.each(["ltr", "rtl"])("uses the physical right pane direction in %s layouts", (direction) => {
    pane.dataset["side"] = "right";
    wrapper.dir = direction;
    const separator = mountHandle();
    flushSync(() => separator.focus());
    press("ArrowRight");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("284px");
    press("ArrowLeft");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("300px");
    expect(separator).toHaveAttribute("aria-valuenow", "300");
    expect(document.activeElement).toBe(separator);
  });

  it("keeps pointer and keyboard direction consistent for a right pane", () => {
    pane.dataset["side"] = "right";
    start();
    send("pointermove", 150);
    runFrames();
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("250px");
    send("pointerup", 200);
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("224px");
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE}=224px`);
    expectReleased();
  });

  it("retires an active pointer drag before a keyboard adjustment owns the width", () => {
    start();
    send("pointermove", 150);
    runFrames();
    send("pointermove", 180);
    expect(frames.size).toBe(1);

    press("ArrowRight");
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("366px");
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE}=366px`);
    expectReleased();
    expect(host.querySelector('[aria-hidden="true"]')).toBeNull();
    send("pointerup", 999);
    runFrames();
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("366px");
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE}=366px`);
  });

  it("releases listeners, frames and prior styles when unmounted mid-drag", () => {
    start();
    send("pointermove", 150);
    expect(frames.size).toBe(1);
    flushSync(() => root.unmount());
    expectReleased();
    runFrames();
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("");
    expect(document.cookie).not.toContain(SIDEBAR_WIDTH_COOKIE);
  });

  it("cancels only its own pointer and never persists an abandoned drag", () => {
    start();
    send("pointermove", 150);
    send("pointercancel", 999, 2);
    expect(frames.size).toBe(1);
    send("pointercancel", 150);
    expectReleased();
    expect(document.cookie).not.toContain(SIDEBAR_WIDTH_COOKIE);
    expect(host.querySelector('[aria-hidden="true"]')).toBeNull();
  });

  it("replaces a reentrant drag without retaining old handlers or accepting its pointerup", () => {
    start();
    send("pointermove", 150);
    start(2);
    expect({ frames: frames.size, move: active("pointermove"), up: active("pointerup") }).toEqual({
      frames: 0,
      move: 1,
      up: 1,
    });
    send("pointerup", 999);
    expect(document.cookie).not.toContain(SIDEBAR_WIDTH_COOKIE);
    send("pointermove", 170, 2);
    send("pointerup", 180, 2);
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("380px");
    expectReleased();
  });

  it("coalesces moves, clamps width and persists the final pointer coordinate", () => {
    start();
    for (let x = 101; x <= 200; x++) send("pointermove", x);
    expect(frames.size).toBe(1);
    runFrames();
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("400px");
    send("pointerup", 900);
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("576px");
    expect(document.cookie).toContain(`${SIDEBAR_WIDTH_COOKIE}=576px`);
    expectReleased();
  });

  it("ignores foreign pointer work, clamps the minimum and releases on window blur", () => {
    start();
    send("pointermove", 999, 2);
    expect(frames.size).toBe(0);
    send("pointermove", 150);
    send("pointerup", 999, 2);
    expect(frames.size).toBe(1);
    expect(document.cookie).not.toContain(SIDEBAR_WIDTH_COOKIE);
    send("pointerup", 0);
    expect(wrapper.style.getPropertyValue("--sidebar-width")).toBe("224px");
    expectReleased();
    const previousCookie = document.cookie;
    start();
    send("pointermove", 200);
    flushSync(() => window.dispatchEvent(new Event("blur")));
    expectReleased();
    expect(document.cookie).toBe(previousCookie);
  });
});
