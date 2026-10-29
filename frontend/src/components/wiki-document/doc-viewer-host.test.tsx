// @vitest-environment happy-dom

import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { describe, expect, it } from "vitest";

import {
  DOCUMENT_LOADING_DELAY_MS,
  DOCUMENT_LOADING_EXIT_MS,
  DOCUMENT_LOADING_MIN_MS,
  DOCUMENT_LOADING_SLOW_MS,
  DocViewerHost,
  HTML_IFRAME_CLASS_NAME,
  HTML_IFRAME_SANDBOX,
  htmlContentUrl,
  viewerKey,
} from "@/components/wiki-document/doc-viewer-host";
import { type ActiveDoc, useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { MIME_DOC, MIME_HTML } from "@/types/wiki";

vi.mock("@/lib/file-urls", () => ({ embedUrlFor: () => "about:blank" }));

let nextFrameId = 0;
let reducedMotion = false;
const ANIMATION_FRAMES = new Map<number, FrameRequestCallback>();
const subscribeToMotion = vi.fn<(event: string, callback: () => void) => void>();
const unsubscribeFromMotion = vi.fn<(event: string, callback: () => void) => void>();
const readMotionPreference = vi.fn(() => ({
  addEventListener: subscribeToMotion,
  get matches() {
    return reducedMotion;
  },
  media: "(prefers-reduced-motion: reduce)",
  onchange: null,
  removeEventListener: unsubscribeFromMotion,
}));

beforeEach(() => {
  vi.useFakeTimers();
  reducedMotion = false;
  ANIMATION_FRAMES.clear();
  subscribeToMotion.mockClear();
  unsubscribeFromMotion.mockClear();
  readMotionPreference.mockClear();
  useDocViewerStore.getState().reset();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    nextFrameId += 1;
    ANIMATION_FRAMES.set(nextFrameId, callback);
    return nextFrameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    ANIMATION_FRAMES.delete(id);
  });
  vi.stubGlobal("matchMedia", readMotionPreference);
});

afterEach(() => {
  useDocViewerStore.getState().reset();
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("isolated Drive HTML viewer", () => {
  it("grants scripts and external popups without trusted-origin access", () => {
    expect(HTML_IFRAME_SANDBOX).toBe("allow-popups allow-popups-to-escape-sandbox allow-scripts");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-same-origin");
    expect(HTML_IFRAME_SANDBOX).not.toContain("allow-top-navigation");
    expect(HTML_IFRAME_CLASS_NAME).toContain("html-theme-frame");
    expect(htmlContentUrl("file/id", "v1")).toBe("/api/files/file%2Fid/content?version=v1");
  });
});

describe("pooled viewer identity", () => {
  const base = { fileId: "file-1", reloadKey: 0, title: "A file" };

  it("keeps Google previews mounted when metadata supplies a newer version", () => {
    expect(viewerKey({ ...base, mimeType: MIME_DOC })).toBe(
      viewerKey({ ...base, mimeType: MIME_DOC, version: "new-version" }),
    );
  });

  it("reloads served HTML when its content version changes", () => {
    expect(viewerKey({ ...base, mimeType: MIME_HTML })).not.toBe(
      viewerKey({ ...base, mimeType: MIME_HTML, version: "new-version" }),
    );
  });

  it("uses reload keys to create a fresh viewer lifecycle", () => {
    expect(viewerKey({ ...base, mimeType: MIME_DOC, reloadKey: 0 })).not.toBe(
      viewerKey({ ...base, mimeType: MIME_DOC, reloadKey: 1 }),
    );
  });

  it("shares one reduced-motion subscription across four mounted viewers", () => {
    const view = renderViewer(doc("a"), doc("d"));
    activate(doc("b"));
    activate(doc("c"));
    expect(document.querySelectorAll("iframe")).toHaveLength(4);
    expect(subscribeToMotion).toHaveBeenCalledOnce();
    view.unmount();
    expect(unsubscribeFromMotion).toHaveBeenCalledOnce();
  });

  it("transfers a prewarmed iframe into the active pool without remounting it", () => {
    const view = renderViewer(doc("other"), doc("prepared"));
    const prepared = iframe("prepared");
    expect(prepared).not.toBeNull();
    activate(doc("prepared"));
    expect(useDocViewerStore.getState().prewarm).toBeNull();
    expect(iframe("prepared")).toBe(prepared);
    activate(doc("next"));
    activate(doc("third"));
    activate(doc("fourth"));
    expect(iframe("prepared")).toBeNull();
    expect(document.querySelectorAll("iframe")).toHaveLength(3);
    view.unmount();
  });

  it("reuses one live media query until the mounted viewer subscription retires", () => {
    const view = renderViewer(doc("a"));
    activate(doc("b"));
    activate(doc("c"));
    advance(DOCUMENT_LOADING_DELAY_MS);
    reducedMotion = true;
    flushSync(() => {
      subscribeToMotion.mock.calls[0]?.[1]();
    });
    loadIframe("c");
    advance(DOCUMENT_LOADING_MIN_MS);
    runFrame();
    runFrame();

    expect(iframe("c")?.parentElement?.querySelector("[data-document-loading-overlay]")).toBeNull();
    expect(readMotionPreference).toHaveBeenCalledOnce();
    expect(subscribeToMotion).toHaveBeenCalledOnce();
    view.unmount();
    expect(unsubscribeFromMotion).toHaveBeenCalledOnce();

    const utils = renderViewer(doc("d"));
    expect(readMotionPreference).toHaveBeenCalledTimes(2);
    utils.unmount();
  });

  it("preserves pool priority and fills the bounded viewer list after overlapping sources", () => {
    const view = renderViewer(doc("a"), doc("b"));
    activate(doc("c"));
    flushSync(() => {
      useDocViewerStore.getState().setWarm([doc("a"), doc("e"), doc("f")]);
    });

    expect([...document.querySelectorAll("iframe")].map((entry) => entry.title)).toEqual([
      "c",
      "a",
      "b",
      "e",
    ]);
    view.unmount();
  });

  it("fills permitted background viewers after skipping a formerly active HTML frame", () => {
    const html = { ...doc("html"), mimeType: MIME_HTML, version: "v1" };
    useDocViewerStore.getState().setActive(html, Symbol("previous"));
    const view = renderViewer(doc("a"), doc("b"));
    flushSync(() => {
      useDocViewerStore.getState().setWarm([doc("c")]);
    });

    expect([...document.querySelectorAll("iframe")].map((entry) => entry.title)).toEqual([
      "a",
      "b",
      "c",
    ]);
    view.unmount();
  });

  it("does not subscribe to motion preferences until a viewer is mounted", () => {
    const view = renderViewer(null);
    expect(subscribeToMotion).not.toHaveBeenCalled();
    view.unmount();
  });

  it("coalesces a window resize burst into one geometry read before the next paint", () => {
    const view = renderViewer(doc("a"));
    const initialReads = view.readRect.mock.calls.length;
    for (let index = 0; index < 100; index += 1) window.dispatchEvent(new Event("resize"));
    expect(view.readRect).toHaveBeenCalledTimes(initialReads);
    runFrame();
    expect(view.readRect).toHaveBeenCalledTimes(initialReads + 1);
    view.unmount();
    expect(ANIMATION_FRAMES.size).toBe(0);
  });
});

describe("document-local loading surface", () => {
  it("delays feedback, enforces its minimum, and reveals after the paint gate", () => {
    const view = renderViewer(doc("a"));

    expect(overlay()?.dataset["loadingPhase"]).toBe("masked");
    expect(status()).toBeNull();
    advance(DOCUMENT_LOADING_DELAY_MS - 1);
    expect(status()).toBeNull();
    advance(1);
    expect(status()).toHaveTextContent("Loading document…");
    expect(frame()?.getAttribute("aria-busy")).toBe("true");

    loadIframe("a");
    advance(DOCUMENT_LOADING_MIN_MS - 1);
    expect(overlay()?.dataset["loadingPhase"]).toBe("visible");
    advance(1);
    runFrame();
    runFrame();
    expect(overlay()?.dataset["loadingPhase"]).toBe("exiting");
    advance(DOCUMENT_LOADING_EXIT_MS - 1);
    expect(overlay()).not.toBeNull();
    advance(1);
    expect(overlay()).toBeNull();
    expect(document.querySelector("[aria-busy]")).toBeNull();

    view.unmount();
  });

  it("never flashes the loader when the iframe is ready inside the delay", () => {
    const view = renderViewer(doc("a"));

    loadIframe("a");
    advance(0);
    runFrame();
    runFrame();

    expect(status()).toBeNull();
    expect(overlay()?.querySelector("picture")).toBeNull();
    expect(overlay()?.dataset["loadingPhase"]).toBe("masked-exiting");
    advance(DOCUMENT_LOADING_EXIT_MS);
    expect(overlay()).toBeNull();

    view.unmount();
  });

  it("keeps honest feedback visible and changes long-load copy in place", () => {
    const view = renderViewer(doc("a"));
    advance(DOCUMENT_LOADING_DELAY_MS);
    const statusRow = status();

    advance(DOCUMENT_LOADING_SLOW_MS - DOCUMENT_LOADING_DELAY_MS);

    expect(status()).toBe(statusRow);
    expect(status()).toHaveTextContent("Still loading document…");
    expect(overlay()?.dataset["loadingPhase"]).toBe("visible");

    view.unmount();
  });

  it("promotes a ready prewarm without remounting it or showing feedback", () => {
    const entry = doc("a");
    const view = renderViewer(null, entry);
    const prewarmedIframe = iframe("a");
    loadIframe("a");
    advance(0);

    activate(entry);

    expect(iframe("a")).toBe(prewarmedIframe);
    expect(overlay()).toBeNull();
    expect(status()).toBeNull();

    view.unmount();
  });

  it("announces only the active viewer and ignores a stale pooled load", () => {
    const first = doc("a");
    const second = doc("b");
    const view = renderViewer(first, second);
    advance(DOCUMENT_LOADING_DELAY_MS);
    expect(status()).toHaveTextContent("Loading document…");

    activate(second);
    advance(DOCUMENT_LOADING_DELAY_MS);
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
    expect(iframe("a")?.closest('[aria-hidden="true"]')).not.toBeNull();

    loadIframe("a");
    advance(0);

    expect(status()).toHaveTextContent("Loading document…");
    expect(overlay()?.dataset["loadingPhase"]).toBe("visible");

    view.unmount();
  });

  it("removes the overlay without animation for reduced motion", () => {
    reducedMotion = true;
    const view = renderViewer(doc("a"));
    advance(DOCUMENT_LOADING_DELAY_MS);

    expect(status()).toHaveTextContent("Loading document…");
    loadIframe("a");
    advance(0);
    advance(DOCUMENT_LOADING_MIN_MS);
    runFrame();
    runFrame();

    expect(overlay()).toBeNull();

    view.unmount();
  });

  it("keeps one timer and announcement lifecycle under Strict Mode replay", () => {
    const view = renderViewer(doc("a"), null, true);

    advance(DOCUMENT_LOADING_DELAY_MS);

    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
    expect(status()).toHaveTextContent("Loading document…");
    loadIframe("a");
    advance(DOCUMENT_LOADING_MIN_MS);
    runFrame();
    runFrame();
    advance(DOCUMENT_LOADING_EXIT_MS);
    expect(overlay()).toBeNull();

    view.unmount();
  });
});

function activate(entry: ActiveDoc): void {
  flushSync(() => {
    useDocViewerStore.getState().setActive(entry, Symbol("active"));
  });
}

function advance(milliseconds: number): void {
  flushSync(() => {
    vi.advanceTimersByTime(milliseconds);
  });
}

function doc(fileId: string): ActiveDoc {
  return { fileId, mimeType: MIME_DOC, reloadKey: 0, title: fileId };
}

function frame(): HTMLElement | null {
  return document.querySelector('[aria-busy="true"]');
}

function iframe(title: string): HTMLIFrameElement | null {
  return document.querySelector(`iframe[title="${CSS.escape(title)}"]`);
}

function loadIframe(title: string): void {
  flushSync(() => {
    iframe(title)?.dispatchEvent(new Event("load"));
  });
}

function overlay(): HTMLElement | null {
  return document.querySelector('[data-document-loading-overlay="true"]');
}

function renderViewer(active: ActiveDoc | null, prewarm: ActiveDoc | null = null, strict = false) {
  const container = document.createElement("div");
  const slot = document.createElement("div");
  document.body.append(container, slot);
  const readRect = vi.fn(() => ({
    bottom: 640,
    height: 600,
    left: 0,
    right: 800,
    top: 40,
    width: 800,
  }));
  Object.defineProperty(slot, "getBoundingClientRect", { value: readRect });
  const store = useDocViewerStore.getState();
  store.setSlot(slot, Symbol("slot"));
  if (prewarm) store.setPrewarm(prewarm);
  if (active) store.setActive(active, Symbol("active"));
  const root: Root = createRoot(container);
  flushSync(() => {
    root.render(
      strict ? (
        <StrictMode>
          <DocViewerHost />
        </StrictMode>
      ) : (
        <DocViewerHost />
      ),
    );
  });
  return {
    readRect,
    unmount: () => {
      flushSync(() => root.unmount());
      container.remove();
      slot.remove();
    },
  };
}

function runFrame(): void {
  const callbacks = ANIMATION_FRAMES.values().toArray();
  ANIMATION_FRAMES.clear();
  flushSync(() => {
    for (const callback of callbacks) callback(performance.now());
  });
}

function status(): HTMLElement | null {
  return document.querySelector('[role="status"]');
}
