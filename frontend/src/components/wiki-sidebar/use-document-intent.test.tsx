import Link from "next/link";
import { act } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import {
  DOCUMENT_INTENT_PREFETCH_MS,
  DOCUMENT_INTENT_PREWARM_MS,
  useDocumentIntent,
} from "@/components/wiki-sidebar/use-document-intent";
import { MIME_DOC } from "@/types/wiki";

const MOCKS = vi.hoisted(() => ({
  prefetchQuery: vi.fn().mockResolvedValue(undefined),
  prefetchRoute: vi.fn(),
  strongIntent: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  queryOptions: (options: unknown) => options,
  useQueryClient: () => ({ prefetchQuery: MOCKS.prefetchQuery }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ prefetch: MOCKS.prefetchRoute }),
}));

describe("predictive document intent", () => {
  let container: HTMLDivElement;
  let root: null | Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    setNavigatorHint("connection", undefined);
    setNavigatorHint("deviceMemory", undefined);
    useDocViewerStore.getState().reset();
    MOCKS.prefetchQuery.mockClear();
    MOCKS.prefetchRoute.mockClear();
    MOCKS.strongIntent.mockClear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    if (root !== null) {
      flushSync(() => {
        root?.unmount();
      });
    }
    container.remove();
    useDocViewerStore.getState().reset();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("stages reusable prefetch at 150ms and iframe prewarm at 300ms", () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerover", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREFETCH_MS - 1);
    expect(MOCKS.prefetchQuery).not.toHaveBeenCalled();
    expect(MOCKS.prefetchRoute).not.toHaveBeenCalled();

    advance(1);
    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchQuery.mock.calls[0]?.[0]).toMatchObject({
      queryKey: ["wiki", "file", "doc-1"],
    });
    expect(MOCKS.prefetchRoute).toHaveBeenCalledExactlyOnceWith("/d/doc-1");
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    advance(DOCUMENT_INTENT_PREWARM_MS - DOCUMENT_INTENT_PREFETCH_MS);
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
  });

  it("keeps the initial intent alive while hover and focus overlap", () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerover", { pointerType: "pen" });
    advance(50);
    dispatch(link, "focusin");
    advance(50);
    dispatchPointer(link, "pointerout", { pointerType: "pen" });
    advance(50);

    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
    dispatch(link, "focusout");
    advance(DOCUMENT_INTENT_PREWARM_MS);
    expect(useDocViewerStore.getState().prewarm).toBeNull();
  });

  it("cancels both stages when the final weak-intent signal leaves", () => {
    renderHarness(root);
    const link = getLink(container);
    dispatchPointer(link, "pointerover", { pointerType: "mouse" });
    advance(100);
    dispatchPointer(link, "pointerout", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREWARM_MS);

    expect(MOCKS.prefetchQuery).not.toHaveBeenCalled();
    expect(MOCKS.prefetchRoute).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    dispatch(link, "focusin");
    flushSync(() => {
      root?.unmount();
    });
    root = null;
    advance(DOCUMENT_INTENT_PREWARM_MS);
    expect(MOCKS.prefetchQuery).not.toHaveBeenCalled();
  });

  it("flushes all stages for an unmodified primary pointerdown without rescheduling on focus", () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerdown", { button: 0, pointerType: "touch" });
    dispatch(link, "focusin");

    expect(MOCKS.strongIntent).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchRoute).toHaveBeenCalledOnce();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
    advance(DOCUMENT_INTENT_PREWARM_MS);
    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
  });

  it("ignores modified pointerdown but flushes a keyboard or assistive click", () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerdown", {
      button: 0,
      ctrlKey: true,
      pointerType: "mouse",
    });
    expect(MOCKS.prefetchQuery).not.toHaveBeenCalled();

    dispatch(link, "click", { detail: 0 });
    expect(MOCKS.strongIntent).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchRoute).toHaveBeenCalledOnce();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
  });

  it.each([
    ["Save-Data", { connection: { saveData: true } }],
    ["a hidden tab", { visibility: "hidden" }],
  ])("blocks every speculative stage for %s but not committed navigation", (_name, hints) => {
    if ("connection" in hints) setNavigatorHint("connection", hints.connection);
    if ("visibility" in hints) {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue(
        hints.visibility === "hidden" ? "hidden" : "visible",
      );
    }
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerover", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREWARM_MS);
    expect(MOCKS.prefetchQuery).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    dispatchPointer(link, "pointerdown", { button: 0, pointerType: "mouse" });
    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
  });

  it.each([
    ["slow-2g", { connection: { effectiveType: "slow-2g" } }],
    ["2g", { connection: { effectiveType: "2g" } }],
    ["2GB", { deviceMemory: 2 }],
  ])("allows reusable speculation but blocks speculative iframe work on %s", (_name, hints) => {
    if ("connection" in hints) setNavigatorHint("connection", hints.connection);
    if ("deviceMemory" in hints) setNavigatorHint("deviceMemory", hints.deviceMemory);
    renderHarness(root);

    dispatchPointer(getControl(), "pointerover", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREWARM_MS);

    expect(MOCKS.prefetchQuery).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchRoute).toHaveBeenCalledOnce();
    expect(useDocViewerStore.getState().prewarm).toBeNull();
  });

  it("skips active, inaccessible, and form-style disabled targets", () => {
    renderHarness(root, true);
    const link = getLink(container);
    dispatchPointer(link, "pointerover", { pointerType: "mouse" });
    dispatchPointer(link, "pointerdown", { button: 0, pointerType: "mouse" });
    dispatch(link, "click", { detail: 0 });
    advance(DOCUMENT_INTENT_PREWARM_MS);

    expect(MOCKS.strongIntent).not.toHaveBeenCalled();
    expect(MOCKS.prefetchQuery).not.toHaveBeenCalled();
    expect(MOCKS.prefetchRoute).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();
  });
});

function Harness({ disabled = false }: { disabled?: boolean }) {
  const fileId = "doc-1";
  const intent = useDocumentIntent({
    disabled,
    fileId,
    mimeType: MIME_DOC,
    onStrongIntent: MOCKS.strongIntent,
    title: "Document one",
  });
  return (
    <Link href={`/d/${fileId}`} id="document-intent-control" {...intent}>
      Document one
    </Link>
  );
}

function renderHarness(root: null | Root, disabled = false): void {
  flushSync(() => {
    root?.render(<Harness disabled={disabled} />);
  });
}

function getLink(_container: HTMLElement): HTMLElement {
  return getControl();
}

function advance(milliseconds: number): void {
  act(() => {
    vi.advanceTimersByTime(milliseconds);
  });
}

function setNavigatorHint(key: string, value: unknown): void {
  Object.defineProperty(navigator, key, { configurable: true, value });
}

function dispatch(element: HTMLElement, type: string, init: MouseEventInit = {}): void {
  act(() => {
    element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...init }));
  });
}

function dispatchPointer(
  element: HTMLElement,
  type: string,
  init: MouseEventInit & { pointerType: string },
): void {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(event, "pointerType", { value: init.pointerType });
  act(() => {
    element.dispatchEvent(event);
  });
}

function getControl(): HTMLElement {
  const control = document.getElementById("document-intent-control");
  if (control === null) throw new Error("Expected document intent control");
  return control;
}
