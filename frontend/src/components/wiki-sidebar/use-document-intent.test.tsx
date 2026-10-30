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
import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";

const MOCKS = vi.hoisted(() => ({
  getQueryData: vi.fn(() => ({
    breadcrumb: [],
    canEdit: false,
    driveId: "drive-1",
    id: "doc-1",
    mimeType: "application/vnd.google-apps.document",
    name: "Document one",
  })),
  query: vi.fn().mockResolvedValue(undefined),
  prefetchRoute: vi.fn(),
  strongIntent: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  queryOptions: (options: unknown) => options,
  useQueryClient: () => ({
    query: async (...options: unknown[]) => {
      await MOCKS.query(...options);
      return MOCKS.getQueryData();
    },
    getQueryState: vi.fn(),
  }),
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
    MOCKS.query.mockReset().mockResolvedValue(undefined);
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

  it("stages reusable prefetch at 150ms and iframe prewarm at 300ms", async () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerover", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREFETCH_MS - 1);
    expect(MOCKS.query).not.toHaveBeenCalled();
    expect(MOCKS.prefetchRoute).not.toHaveBeenCalled();

    advance(1);
    expect(MOCKS.query).toHaveBeenCalledOnce();
    expect(MOCKS.query.mock.calls[0]?.[0]).toMatchObject({
      queryKey: ["wiki", "file", "doc-1"],
    });
    expect(MOCKS.prefetchRoute).toHaveBeenCalledExactlyOnceWith("/d/doc-1");
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    advance(DOCUMENT_INTENT_PREWARM_MS - DOCUMENT_INTENT_PREFETCH_MS);
    await flushAsync();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
  });

  it("takes one current navigator snapshot at each iframe-policy boundary", async () => {
    const connection = vi.fn(() => ({ effectiveType: "4g", saveData: false }));
    Object.defineProperty(navigator, "connection", { configurable: true, get: connection });
    renderHarness(root);
    dispatchPointer(getLink(container), "pointerover", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREWARM_MS);
    await flushAsync();

    expect(connection).toHaveBeenCalledTimes(4);
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

    expect(MOCKS.query).toHaveBeenCalledOnce();
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

    expect(MOCKS.query).not.toHaveBeenCalled();
    expect(MOCKS.prefetchRoute).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    dispatch(link, "focusin");
    flushSync(() => {
      root?.unmount();
    });
    root = null;
    advance(DOCUMENT_INTENT_PREWARM_MS);
    expect(MOCKS.query).not.toHaveBeenCalled();
  });

  it.each(["hover leaves", "row unmounts", "Save-Data", "hidden tab", "disabled row"])(
    "discards authorized weak prewarm after %s while retaining its reusable authorization",
    async (scenario) => {
      const authorization = Promise.withResolvers<undefined>();
      MOCKS.query.mockReturnValue(authorization.promise);
      renderHarness(root);
      const link = getLink(container);
      dispatchPointer(link, "pointerover", { pointerType: "mouse" });
      advance(DOCUMENT_INTENT_PREWARM_MS);
      expect(MOCKS.query).toHaveBeenCalledTimes(2);

      switch (scenario) {
        case "hover leaves": {
          dispatchPointer(link, "pointerout", { pointerType: "mouse" });
          break;
        }
        case "row unmounts": {
          flushSync(() => root?.unmount());
          root = null;
          break;
        }
        case "Save-Data": {
          setNavigatorHint("connection", { saveData: true });
          break;
        }
        case "hidden tab": {
          vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
          break;
        }
        case "disabled row": {
          renderHarness(root, true);
          break;
        }
        default: {
          throw new Error(`Unexpected prewarm retirement scenario: ${scenario}`);
        }
      }
      authorization.resolve(undefined);
      await flushAsync();

      expect(useDocViewerStore.getState().prewarm).toBeNull();
      expect(MOCKS.query).toHaveBeenCalledTimes(2);
    },
  );

  it("does not attach an old hover authorization to a new hover generation", async () => {
    const authorization = Promise.withResolvers<undefined>();
    MOCKS.query.mockReturnValue(authorization.promise);
    renderHarness(root);
    const link = getLink(container);
    dispatchPointer(link, "pointerover", { pointerType: "mouse" });
    advance(DOCUMENT_INTENT_PREWARM_MS);
    dispatchPointer(link, "pointerout", { pointerType: "mouse" });
    dispatchPointer(link, "pointerover", { pointerType: "mouse" });

    authorization.resolve(undefined);
    await flushAsync();
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    advance(DOCUMENT_INTENT_PREWARM_MS);
    await flushAsync();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
  });

  it("lets committed navigation publish after its sidebar row unmounts", async () => {
    const authorization = Promise.withResolvers<undefined>();
    MOCKS.query.mockReturnValue(authorization.promise);
    renderHarness(root);
    dispatchPointer(getLink(container), "pointerdown", { button: 0, pointerType: "mouse" });
    flushSync(() => root?.unmount());
    root = null;
    authorization.resolve(undefined);
    await flushAsync();

    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
    expect(MOCKS.query).toHaveBeenCalledOnce();
  });

  it("discards committed prewarm when its browser session retires", async () => {
    const owner = {};
    claimBrowserSession(owner);
    const authorization = Promise.withResolvers<undefined>();
    MOCKS.query.mockReturnValue(authorization.promise);
    renderHarness(root);
    dispatchPointer(getLink(container), "pointerdown", { button: 0, pointerType: "mouse" });
    retireBrowserSession(owner);
    authorization.resolve(undefined);
    await flushAsync();

    expect(useDocViewerStore.getState().prewarm).toBeNull();
    expect(MOCKS.query).toHaveBeenCalledOnce();
  });

  it("flushes all stages for an unmodified primary pointerdown without rescheduling on focus", async () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerdown", { button: 0, pointerType: "touch" });
    await flushAsync();
    dispatch(link, "focusin");

    expect(MOCKS.strongIntent).toHaveBeenCalledOnce();
    expect(MOCKS.query).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchRoute).toHaveBeenCalledOnce();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
    advance(DOCUMENT_INTENT_PREWARM_MS);
    expect(MOCKS.query).toHaveBeenCalledOnce();
  });

  it("ignores modified pointerdown but flushes a keyboard or assistive click", async () => {
    renderHarness(root);
    const link = getLink(container);

    dispatchPointer(link, "pointerdown", {
      button: 0,
      ctrlKey: true,
      pointerType: "mouse",
    });
    expect(MOCKS.query).not.toHaveBeenCalled();

    dispatch(link, "click", { detail: 0 });
    await flushAsync();
    expect(MOCKS.strongIntent).toHaveBeenCalledOnce();
    expect(MOCKS.query).toHaveBeenCalledOnce();
    expect(MOCKS.prefetchRoute).toHaveBeenCalledOnce();
    expect(useDocViewerStore.getState().prewarm).toMatchObject({ fileId: "doc-1" });
  });

  it.each([
    ["Save-Data", { connection: { saveData: true } }],
    ["a hidden tab", { visibility: "hidden" }],
  ])("blocks every speculative stage for %s but not committed navigation", async (_name, hints) => {
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
    expect(MOCKS.query).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();

    dispatchPointer(link, "pointerdown", { button: 0, pointerType: "mouse" });
    await flushAsync();
    expect(MOCKS.query).toHaveBeenCalledOnce();
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

    expect(MOCKS.query).toHaveBeenCalledOnce();
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
    expect(MOCKS.query).not.toHaveBeenCalled();
    expect(MOCKS.prefetchRoute).not.toHaveBeenCalled();
    expect(useDocViewerStore.getState().prewarm).toBeNull();
  });
});

function Harness({ disabled = false }: { disabled?: boolean }) {
  const fileId = "doc-1";
  const intent = useDocumentIntent({
    disabled,
    fileId,
    onStrongIntent: MOCKS.strongIntent,
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

async function flushAsync(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
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
