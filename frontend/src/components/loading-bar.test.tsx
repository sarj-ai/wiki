// @vitest-environment happy-dom

import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { GlobalLoadingBar, isForegroundPrimaryLoad } from "@/components/loading-bar";
import { useDocViewerStore, viewerIdentity } from "@/components/wiki-document/doc-viewer-store";
import { MIME_DOC } from "@/types/wiki";

const MOCKS = vi.hoisted(() => ({ fetching: 0, pathname: "/wiki/a" }));

vi.mock("@tanstack/react-query", () => ({
  useIsFetching: () => MOCKS.fetching,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => MOCKS.pathname,
}));

describe("global loading bar", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    MOCKS.fetching = 0;
    MOCKS.pathname = "/wiki/a";
    useDocViewerStore.getState().reset();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    container.remove();
    useDocViewerStore.getState().reset();
    vi.useRealTimers();
  });

  it("only renders while a foreground document has no data", () => {
    render(root);
    expect(indicator()).toBeNull();

    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    expect(indicator()).not.toBeNull();
    expect(status()).toHaveTextContent("Loading document");

    MOCKS.fetching = 0;
    render(root);
    expect(indicator()).not.toBeNull();
    flushSync(() => vi.advanceTimersByTime(299));
    expect(indicator()).not.toBeNull();
    flushSync(() => vi.advanceTimersByTime(1));
    expect(indicator()).toBeNull();
  });

  it("cannot remain visibly stuck after the watchdog expires", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));

    flushSync(() => vi.advanceTimersByTime(29_849));
    expect(indicator()).not.toBeNull();

    flushSync(() => vi.advanceTimersByTime(1));

    expect(indicator()).toBeNull();
  });

  it("starts a fresh watchdog when navigation supersedes an in-flight load", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    const firstIndicator = indicator();
    const firstSegment = firstIndicator?.firstElementChild;
    flushSync(() => {
      vi.advanceTimersByTime(29_000);
    });

    MOCKS.pathname = "/wiki/b";
    render(root);
    expect(indicator()).toBe(firstIndicator);
    expect(indicator()?.firstElementChild).not.toBe(firstSegment);
    flushSync(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(indicator()).not.toBeNull();

    flushSync(() => {
      vi.advanceTimersByTime(28_000);
    });
    expect(indicator()).toBeNull();
  });

  it("starts a fresh watchdog after a same-path load settles", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));

    MOCKS.fetching = 0;
    render(root);
    flushSync(() => vi.advanceTimersByTime(300));
    expect(indicator()).toBeNull();
    MOCKS.fetching = 1;
    render(root);
    expect(indicator()).toBeNull();
    flushSync(() => vi.advanceTimersByTime(150));
    flushSync(() => vi.advanceTimersByTime(2_000));

    expect(indicator()).not.toBeNull();
  });

  it("keeps the same moving segment when loading resumes during the minimum", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    const firstIndicator = indicator();

    MOCKS.fetching = 0;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(200));

    expect(indicator()).toBe(firstIndicator);
  });

  it("starts a visible cycle for a new path after the prior path was suppressed", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(30_000));
    expect(indicator()).toBeNull();
    expect(status()).toHaveTextContent("Still loading document");

    MOCKS.pathname = "/wiki/b";
    render(root);
    flushSync(() => vi.advanceTimersByTime(149));
    expect(indicator()).toBeNull();
    flushSync(() => vi.advanceTimersByTime(1));
    expect(indicator()).not.toBeNull();
  });

  it("provides a static full-width reduced-motion state", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));

    const segment = indicator()?.firstElementChild;
    expect(segment).toHaveClass("animate-loading-bar", "left-0", "w-1/3", "will-change-transform");
  });

  it("does not flash for a foreground load shorter than 150ms", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(149));
    expect(indicator()).toBeNull();

    MOCKS.fetching = 0;
    render(root);
    flushSync(() => vi.runAllTimers());
    expect(indicator()).toBeNull();
  });

  it("stays active through metadata completion until the selected iframe is ready", () => {
    const active = { fileId: "doc-1", mimeType: MIME_DOC, reloadKey: 0, title: "Roadmap" };
    const activeOwner = Symbol("active");
    const viewerOwner = Symbol("viewer");
    const store = useDocViewerStore.getState();

    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    const metadataIndicator = indicator();

    flushSync(() => {
      store.setActive(active, activeOwner);
      store.markViewerLoading(viewerIdentity(active), viewerOwner);
    });
    MOCKS.fetching = 0;
    render(root);
    expect(indicator()).toBe(metadataIndicator);

    flushSync(() => {
      useDocViewerStore.getState().markViewerReady(viewerIdentity(active), viewerOwner);
    });
    flushSync(() => vi.advanceTimersByTime(299));
    expect(indicator()).not.toBeNull();
    flushSync(() => vi.advanceTimersByTime(1));
    expect(indicator()).toBeNull();
  });

  it("does not start progress for a prewarmed iframe that is already ready", () => {
    const active = { fileId: "doc-1", mimeType: MIME_DOC, reloadKey: 0, title: "Roadmap" };
    const store = useDocViewerStore.getState();
    const viewerOwner = Symbol("viewer");
    store.markViewerLoading(viewerIdentity(active), viewerOwner);
    store.markViewerReady(viewerIdentity(active), viewerOwner);
    store.setActive(active, Symbol("active"));

    render(root);
    flushSync(() => vi.advanceTimersByTime(30_000));

    expect(indicator()).toBeNull();
  });

  it("survives StrictMode effect replay without duplicate or stale timers", () => {
    MOCKS.fetching = 1;
    flushSync(() => {
      root.render(
        <StrictMode>
          <GlobalLoadingBar />
        </StrictMode>,
      );
    });
    flushSync(() => vi.advanceTimersByTime(150));
    expect(indicator()).not.toBeNull();

    MOCKS.fetching = 0;
    flushSync(() => {
      root.render(
        <StrictMode>
          <GlobalLoadingBar />
        </StrictMode>,
      );
    });
    flushSync(() => vi.advanceTimersByTime(300));
    expect(indicator()).toBeNull();
    flushSync(() => vi.runAllTimers());
    expect(indicator()).toBeNull();
  });
});

describe("foreground loading classification", () => {
  it("shows only tagged, observed queries with no cached data", () => {
    expect(
      isForegroundPrimaryLoad({
        getObserversCount: () => 1,
        meta: { foregroundPrimaryLoad: true },
        state: { data: undefined },
      }),
    ).toBe(true);
  });

  it.each([
    ["untagged query", {}, undefined, 1],
    ["bare prefetch", { foregroundPrimaryLoad: true }, undefined, 0],
    ["cached refetch", { foregroundPrimaryLoad: true }, { id: "cached" }, 1],
  ])("hides a %s", (_name, meta, data, observers) => {
    expect(
      isForegroundPrimaryLoad({
        getObserversCount: () => observers,
        meta,
        state: { data },
      }),
    ).toBe(false);
  });
});

function render(root: Root): void {
  flushSync(() => {
    root.render(<GlobalLoadingBar />);
  });
}

function indicator(): HTMLElement | null {
  return document.body.querySelector('[data-loading-indicator="true"]');
}

function status(): HTMLElement | null {
  return document.body.querySelector('[role="status"]');
}
