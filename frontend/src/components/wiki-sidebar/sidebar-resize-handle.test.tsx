import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
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
  let gap: HTMLDivElement;
  let frames: Map<number, FrameRequestCallback>;
  let nextFrame: number;
  let add: MockInstance<typeof window.addEventListener>;
  let remove: MockInstance<typeof window.removeEventListener>;

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
    host = document.createElement("div");
    wrapper = document.createElement("div");
    wrapper.dataset["slot"] = "sidebar-wrapper";
    const inner = document.createElement("div");
    inner.dataset["slot"] = "sidebar-inner";
    vi.spyOn(inner, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 300, 0));
    gap = document.createElement("div");
    gap.dataset["slot"] = "sidebar-gap";
    gap.style.transition = "width 200ms";
    wrapper.append(inner, gap, host);
    document.body.append(wrapper);
    document.body.style.cursor = "crosshair";
    document.body.style.userSelect = "text";
    clearWidthCookie();
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

  function active(type: string) {
    const removed = remove.mock.calls.filter(([event]) => event === type);
    return add.mock.calls.filter(
      ([event, handler]) => event === type && !removed.some(([, released]) => handler === released),
    ).length;
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
