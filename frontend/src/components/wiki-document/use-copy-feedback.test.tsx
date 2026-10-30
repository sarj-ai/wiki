import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { useCopyFeedback } from "./use-copy-feedback";

describe("copy feedback lifecycle", () => {
  let host: HTMLDivElement;
  let root: Root;
  let feedback: ReturnType<typeof useCopyFeedback>;

  function Probe({ identity }: { identity: string }) {
    feedback = useCopyFeedback(identity);
    return <span>{String(feedback.copied)}</span>;
  }

  function render(identity: string) {
    flushSync(() => root.render(<Probe identity={identity} />));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    host = document.createElement("div");
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    vi.useRealTimers();
  });

  it("keeps one timer and extends feedback from the latest successful copy", () => {
    render("file-a");
    flushSync(() => feedback.showCopied());
    flushSync(() => vi.advanceTimersByTime(1_000));
    flushSync(() => feedback.showCopied());
    expect(vi.getTimerCount()).toBe(1);
    flushSync(() => vi.advanceTimersByTime(1_999));
    expect(host.textContent).toBe("true");
    flushSync(() => vi.advanceTimersByTime(1));
    expect(host.textContent).toBe("false");
  });

  it("releases its timer when unmounted", () => {
    render("file-a");
    flushSync(() => feedback.showCopied());
    flushSync(() => root.render(null));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ignores a late clipboard result from the previous document", () => {
    render("file-a");
    const oldFeedback = feedback.showCopied;
    render("file-b");
    flushSync(() => feedback.showCopied());
    flushSync(oldFeedback);
    expect(host.textContent).toBe("true");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("does not resurrect feedback when a document identity returns", () => {
    render("file-a");
    flushSync(() => feedback.showCopied());
    render("file-b");
    render("file-a");
    expect(host.textContent).toBe("false");
    expect(vi.getTimerCount()).toBe(0);
  });
});
