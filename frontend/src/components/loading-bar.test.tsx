// @vitest-environment happy-dom

import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { GlobalLoadingBar, isForegroundPrimaryLoad } from "@/components/loading-bar";

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
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it("only renders while a foreground document has no data", () => {
    render(root);
    expect(progressBar()).toBeNull();

    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    expect(progressBar()).toHaveAttribute("aria-label", "Loading document");

    MOCKS.fetching = 0;
    render(root);
    expect(progressBar()).toBeNull();
  });

  it("cannot remain visibly stuck after the watchdog expires", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));

    flushSync(() => vi.advanceTimersByTime(29_849));
    expect(progressBar()).not.toBeNull();

    flushSync(() => vi.advanceTimersByTime(1));

    expect(progressBar()).toBeNull();
  });

  it("starts a fresh watchdog when navigation supersedes an in-flight load", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    flushSync(() => {
      vi.advanceTimersByTime(29_000);
    });

    MOCKS.pathname = "/wiki/b";
    render(root);
    flushSync(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(progressBar()).not.toBeNull();

    flushSync(() => {
      vi.advanceTimersByTime(28_000);
    });
    expect(progressBar()).toBeNull();
  });

  it("starts a fresh watchdog after a same-path load settles", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    flushSync(() => vi.advanceTimersByTime(29_000));

    MOCKS.fetching = 0;
    render(root);
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));
    flushSync(() => vi.advanceTimersByTime(2_000));

    expect(progressBar()).not.toBeNull();
  });

  it("provides a static full-width reduced-motion state", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(150));

    const indicator = progressBar()?.firstElementChild;
    expect(indicator).toHaveClass("motion-reduce:animate-none", "motion-reduce:w-full");
  });

  it("does not flash for a foreground load shorter than 150ms", () => {
    MOCKS.fetching = 1;
    render(root);
    flushSync(() => vi.advanceTimersByTime(149));
    expect(progressBar()).toBeNull();

    MOCKS.fetching = 0;
    render(root);
    flushSync(() => vi.runAllTimers());
    expect(progressBar()).toBeNull();
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

function progressBar(): HTMLElement | null {
  return document.body.querySelector('[role="progressbar"]');
}
